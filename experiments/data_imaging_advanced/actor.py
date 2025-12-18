import logging
import json
import pathlib
import asyncio
import numpy as np
import os
import io
import base64
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import ray

# Constants
PLACEHOLDER_CLASSIFICATION = None  # Will be auto-assigned
PLACEHOLDER_SUMMARY = "Pattern matching complete. This workout has been successfully mapped to the 64-dimensional latent space."
PLACEHOLDER_PROMPT = "(not yet generated)"
LATENT_DIM = 64  # 64-dim latent space

NORM_BOUNDS = {
    "heart_rate": (50, 200),
    "calories_per_min": (0, 20),
    "speed_kph": (0, 15)
}

logger = logging.getLogger(__name__)
experiment_dir = pathlib.Path(__file__).parent
templates_dir = experiment_dir / "templates"
MODEL_PATH = experiment_dir / "workout_encoder.pth"

@ray.remote
class ExperimentActor:
    def __init__(self, mongo_uri: str, db_name: str, write_scope: str, read_scopes: list[str]):
        self.write_scope = write_scope
        self.read_scopes = read_scopes
        self.vector_index_name = f"{write_scope}_workout_vector_index"
        
        # Ensure write_scope is in read_scopes
        if write_scope not in self.read_scopes:
            self.read_scopes.append(write_scope)

        # Lazy load dependencies
        try:
            import torch
            import torch.nn as nn
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            from PIL import Image
            from fastapi.templating import Jinja2Templates
            from pymongo import MongoClient
            
            self.torch = torch
            self.nn = nn
            self.plt = plt
            self.Image = Image
            self.templates = Jinja2Templates(directory=str(templates_dir))
            
            # Connect to DB directly for the actor (standard pattern)
            self.client = MongoClient(mongo_uri)
            self.db = self.client[db_name]
            self.collection = self.db[f"{write_scope}_workouts"]
            
            # --- Define Model Architecture ---
            class Autoencoder(nn.Module):
                def __init__(self, latent_dim=LATENT_DIM):
                    super(Autoencoder, self).__init__()
                    # Encoder: Takes (Batch, 3, 64) -> (Batch, latent_dim)
                    self.encoder = nn.Sequential(
                        nn.Conv1d(3, 16, kernel_size=3, padding=1),  # (B, 16, 64)
                        nn.BatchNorm1d(16),
                        nn.ReLU(),
                        nn.MaxPool1d(2),  # (B, 16, 32)
                        nn.Conv1d(16, 32, kernel_size=3, padding=1), # (B, 32, 32)
                        nn.BatchNorm1d(32),
                        nn.ReLU(),
                        nn.MaxPool1d(2),  # (B, 32, 16)
                        nn.Conv1d(32, 64, kernel_size=3, padding=1), # (B, 64, 16)
                        nn.BatchNorm1d(64),
                        nn.ReLU(),
                        nn.MaxPool1d(2),  # (B, 64, 8)
                        nn.Flatten(),     # (B, 64 * 8) = (B, 512)
                        nn.Linear(64 * 8, latent_dim) # (B, 64)
                    )
                    
                    # Decoder: Takes (Batch, latent_dim) -> (Batch, 3, 64)
                    self.decoder = nn.Sequential(
                        nn.Linear(latent_dim, 64 * 8),
                        nn.ReLU(),
                        nn.Unflatten(1, (64, 8)),
                        nn.Conv1d(64, 64, kernel_size=3, padding=1),
                        nn.BatchNorm1d(64),
                        nn.ReLU(),
                        nn.Upsample(scale_factor=2),
                        nn.Conv1d(64, 32, kernel_size=3, padding=1),
                        nn.BatchNorm1d(32),
                        nn.ReLU(),
                        nn.Upsample(scale_factor=2),
                        nn.Conv1d(32, 16, kernel_size=3, padding=1),
                        nn.BatchNorm1d(16),
                        nn.ReLU(),
                        nn.Upsample(scale_factor=2),
                        nn.Conv1d(16, 3, kernel_size=3, padding=1),
                        nn.Sigmoid() # Output 0-1
                    )
                
                def forward(self, x):
                    x = self.encoder(x)
                    x = self.decoder(x)
                    return x

            self.Autoencoder = Autoencoder
            self.model, self.model_is_trained = self._load_or_create_model()
            
            logger.info(f"[{write_scope}-Actor] Initialized with PyTorch Autoencoder (64-dim). Model trained: {self.model_is_trained}")
            
        except ImportError as e:
            logger.critical(f"[{write_scope}-Actor] Failed to load dependencies: {e}")
            self.model = None

    def _load_or_create_model(self):
        """Loads model from disk or creates an untrained one. Returns (model, is_trained)."""
        device = self.torch.device("cpu") # Use CPU for inference in actor
        model = self.Autoencoder(latent_dim=LATENT_DIM).to(device)
        is_trained = False
        
        if MODEL_PATH.exists():
            try:
                state_dict = self.torch.load(str(MODEL_PATH), map_location=device)
                # train_model.py saves the full model.state_dict(), so load it into the full model
                model.load_state_dict(state_dict, strict=True)
                logger.info(f"Loaded trained model from {MODEL_PATH}")
                is_trained = True
            except Exception as e:
                logger.warning(f"Failed to load model from {MODEL_PATH}: {e}. Using untrained model.")
        else:
            logger.warning(f"Model file not found at {MODEL_PATH}. Using untrained model (random weights).")
            
        model.eval()
        return model, is_trained

    def _normalize_data(self, data, min_val, max_val):
        clipped = np.clip(data, min_val, max_val)
        rng = max_val - min_val
        if rng == 0: return np.zeros_like(clipped, dtype=np.float32)
        return ((clipped - min_val) / rng).astype(np.float32)

    def _get_feature_vector(self, doc: dict) -> List[float]:
        """
        Uses the PyTorch Encoder to generate a 64-dim embedding.
        Input: (1, 3, 64) tensor (Batch, Channels, Length)
        Output: List of 64 floats
        """
        if self.model is None:
            return [0.0] * LATENT_DIM

        try:
            ts = doc['time_series']
            hr = self._normalize_data(np.array(ts['heart_rate']), *NORM_BOUNDS["heart_rate"])
            cal = self._normalize_data(np.array(ts['calories_per_min']), *NORM_BOUNDS["calories_per_min"])
            spd = self._normalize_data(np.array(ts['speed_kph']), *NORM_BOUNDS["speed_kph"])
            
            # Stack into (3, 64)
            input_np = np.stack([hr, cal, spd], axis=0).astype(np.float32)
            input_tensor = self.torch.from_numpy(input_np).unsqueeze(0) # Add batch dim: (1, 3, 64)
            
            with self.torch.no_grad():
                # Pass through just the encoder
                embedding = self.model.encoder(input_tensor)
                # embedding is (1, 64)
                vector = embedding.squeeze(0).numpy().tolist()
                
            return vector
        except Exception as e:
            logger.error(f"Error generating feature vector: {e}")
            return [0.0] * LATENT_DIM

    def _generate_viz_images(self, doc: dict):
        """Generates the 8x8 visualization (same as before, still useful for humans)."""
        # Re-use the numpy logic but return base64 strings
        try:
            ts = doc['time_series']
            # Reuse normalization logic but scale to 0-255 uint8 for images
            def norm_uint8(arr, bounds):
                n = self._normalize_data(np.array(arr), *bounds)
                return (n * 255).astype(np.uint8).reshape(8, 8)

            r = norm_uint8(ts['heart_rate'], NORM_BOUNDS['heart_rate'])
            g = norm_uint8(ts['calories_per_min'], NORM_BOUNDS['calories_per_min'])
            b = norm_uint8(ts['speed_kph'], NORM_BOUNDS['speed_kph'])
            
            rgb = np.stack([r, g, b], axis=-1)
            
            def b64(arr, resize=None):
                img = self.Image.fromarray(arr)
                if resize: img = img.resize(resize, self.Image.NEAREST)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return base64.b64encode(buf.getvalue()).decode('utf-8')

            return {
                "combined": b64(rgb, (256, 256)),
                "r": b64(r, (128, 128)), # Grayscale representation
                "g": b64(g, (128, 128)),
                "b": b64(b, (128, 128))
            }
        except Exception as e:
            logger.error(f"Viz gen error: {e}")
            return {}

    def _generate_analysis_summary(self, doc: dict) -> str:
        """Generates a dynamic analysis summary based on workout metrics."""
        w_type = doc.get("workout_type", "Unknown")
        classification = doc.get("ai_classification", "Unclassified")
        
        try:
            ts = doc['time_series']
            hr_avg = np.mean(ts['heart_rate'])
            cal_avg = np.mean(ts['calories_per_min'])
            
            intensity = "Moderate"
            if hr_avg > 150: intensity = "High"
            elif hr_avg < 110: intensity = "Low"
            
            summary = (
                f"This **{w_type}** session is classified as **{classification}** "
                f"with a {intensity.lower()} cardiovascular load (Avg HR: {int(hr_avg)} bpm). "
            )
            
            if w_type == "Cycling" and "watts" in doc:
                summary += f"Power output was sustained at **{doc['watts']}W**, indicating a focused effort. "
            elif w_type == "Strength" and "reps" in doc:
                summary += f"Volume included **{doc['reps']} total reps**, emphasizing muscular endurance. "
                
            summary += "The autoencoder's latent vector places this session in a cluster of similarly structured metabolic profiles."
            return summary
            
        except Exception as e:
            return f"Analysis unavailable: {str(e)}"

    def _create_synthetic_data(self, suffix: int) -> dict:
        """Polymorphic data generation."""
        np.random.seed(suffix)
        t = np.linspace(0, 2 * np.pi, 64)
        
        # Basic patterns
        hr = 110 + (suffix % 7) * 5 + 60 * np.sin(t) + np.random.rand(64) * 10
        cal = 6 + (suffix % 5) * 1 + 4 * np.sin(t) + np.random.rand(64) * 2
        spd = 4.0 + (suffix % 6) * 0.5 + np.random.rand(64) * 0.5
        
        doc = {
            "_id": f"workout_{suffix}",
            "user_id": f"user_{suffix % 5}",
            "start_time": datetime.now(timezone.utc),
            "duration_minutes": 64,
            "workout_type": np.random.choice(["Run", "Strength", "Cycling", "Yoga"]),
            "time_series": {
                "heart_rate": np.clip(hr, 50, 200).tolist(),
                "calories_per_min": np.clip(cal, 0, 20).tolist(),
                "speed_kph": np.clip(spd, 0, 15).tolist()
            }
        }
        
        # Add polymorphic fields
        classification = "Standard Session"
        
        if doc['workout_type'] == "Strength":
            doc['reps'] = int(np.random.randint(20, 100))
            classification = "Strength Training"
        elif doc['workout_type'] == "Cycling":
            watts = int(np.random.randint(100, 400))
            doc['watts'] = watts
            if watts > 250:
                classification = "High Intensity Cycling"
            else:
                classification = "Endurance Ride"
        elif doc['workout_type'] == "Run":
            # Simple heuristic for classification
            if np.mean(hr) > 160:
                classification = "Threshold Run"
            else:
                classification = "Aerobic Base Run"
        elif doc['workout_type'] == "Yoga":
            classification = "Active Recovery"
            
        doc['ai_classification'] = classification
        doc['ai_summary'] = self._generate_analysis_summary(doc)

        return doc

    # --- Public Methods exposed to Routes ---

    async def render_gallery_page(self, request_context):
        # Auto-seed if empty
        if self.collection.count_documents({}) == 0:
            logger.info("Collection empty, seeding initial batch...")
            await self.generate_batch(10)

        cursor = self.collection.find({}).sort("_id", -1).limit(50)
        workouts = list(cursor)
        
        # Pre-render HTML for grid
        grid_html = ""
        for w in workouts:
            try:
                viz = self._generate_viz_images(w)
                wid = w['_id'].split('_')[-1]
                grid_html += f"""
                <div class="collection-item">
                    <a href="workout/{wid}">
                        <img src="data:image/png;base64,{viz.get('combined')}" alt="Workout {wid}">
                        <p>#{wid} ({w['workout_type']})</p>
                    </a>
                </div>
                """
            except Exception: continue
            
        return self.templates.get_template("index.html").render(
            request=None, collection_images_html=grid_html, model_is_trained=self.model_is_trained
        )

    async def render_detail_page(self, workout_id: int, request_context):
        # Try standard ID format
        doc_id = f"workout_{workout_id}"
        doc = self.collection.find_one({"_id": doc_id})
        
        # Fallback: Try "workout_rad_" format (used in demo scripts/legacy data)
        if not doc:
            alt_id = f"workout_rad_{workout_id}"
            doc = self.collection.find_one({"_id": alt_id})
            if doc:
                doc_id = alt_id
                
        if not doc:
            return f"<h1>Workout {doc_id} not found</h1><p>Checked for: <code>workout_{workout_id}</code> and <code>workout_rad_{workout_id}</code></p>"
            
        viz = self._generate_viz_images(doc)
        
        # Vector Search for Twins
        neighbors_html = "<li>No neighbors found</li>"
        if "workout_vector" in doc:
            pipeline = [
                {
                    "$vectorSearch": {
                        "index": self.vector_index_name,
                        "path": "workout_vector",
                        "queryVector": doc["workout_vector"],
                        "numCandidates": 50,
                        "limit": 3,
                        "filter": {"_id": {"$ne": doc_id}}
                    }
                },
                {"$project": {"score": {"$meta": "vectorSearchScore"}, "workout_type": 1}}
            ]
            try:
                neighbors = list(self.collection.aggregate(pipeline))
                if neighbors:
                    neighbors_html = ""
                    for n in neighbors:
                        nid = n['_id'].split('_')[-1]
                        neighbors_html += f"""
                        <li>
                            <a href="{nid}">Workout #{nid}</a> 
                            ({n.get('workout_type')}) - Score: {n['score']:.4f}
                        </li>"""
            except Exception as e:
                neighbors_html = f"<li>Search error: {e}</li>"

        # Prepare context for template
        json_pretty = json.dumps({k:v for k,v in doc.items() if k!='workout_vector'}, indent=2, default=str)
        
        # Fallback or generate summary if it's the old placeholder or missing
        summary = doc.get("ai_summary", PLACEHOLDER_SUMMARY)
        if summary == PLACEHOLDER_SUMMARY:
             summary = self._generate_analysis_summary(doc)
             # Optionally update DB, but for now just show it
        
        return self.templates.get_template("detail.html").render(
            workout_id=workout_id,
            b64_combined=viz.get('combined'),
            b64_r=viz.get('r'),
            b64_g=viz.get('g'),
            b64_b=viz.get('b'),
            json_data_pretty=json_pretty,
            ai_neighbors_html=neighbors_html,
            ai_classification=doc.get("ai_classification", PLACEHOLDER_CLASSIFICATION),
            ai_summary=summary,
            vector_dim=len(doc.get("workout_vector", [])),
            model_is_trained=self.model_is_trained
        )

    async def generate_one(self) -> int:
        # Find next ID
        last = self.collection.find_one(sort=[("_id", -1)])
        if last:
            suffix = int(last['_id'].split('_')[-1]) + 1
        else:
            suffix = 0
            
        doc = self._create_synthetic_data(suffix)
        doc['workout_vector'] = self._get_feature_vector(doc)
        doc['experiment_id'] = self.write_scope
        # ai_classification is already set in _create_synthetic_data
        
        self.collection.insert_one(doc)
        logger.info(f"Generated workout {doc['_id']}")
        return suffix

    async def generate_batch(self, count: int = 100) -> int:
        """Generates a batch of workouts efficiently."""
        last = self.collection.find_one(sort=[("_id", -1)])
        start_suffix = int(last['_id'].split('_')[-1]) + 1 if last else 0
        
        docs = []
        for i in range(count):
            suffix = start_suffix + i
            doc = self._create_synthetic_data(suffix)
            doc['workout_vector'] = self._get_feature_vector(doc)
            doc['experiment_id'] = self.write_scope
            # ai_classification is already set in _create_synthetic_data
            docs.append(doc)
            
        if docs:
            self.collection.insert_many(docs)
            logger.info(f"Generated batch of {count} workouts")
            
        return count

    async def clear_all(self):
        self.collection.delete_many({})

