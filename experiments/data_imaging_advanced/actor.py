# File: /app/experiments/data_imaging_advanced/actor.py

import logging
import json
import pathlib
import asyncio
from typing import List, Dict, Any, Optional
import os
import io
import base64
from datetime import datetime, timezone
import ray

# --- NOTE: ALL HEAVY IMPORTS ARE GONE FROM THE TOP LEVEL ---
# (numpy, matplotlib, httpx, PIL, torch)

# Actor-local paths
experiment_dir = pathlib.Path(__file__).parent
templates_dir = experiment_dir / "templates"

logger = logging.getLogger(__name__)

# --- Constants are still fine at the top level ---
PLACEHOLDER_CLASSIFICATION = "Pending Analysis"
PLACEHOLDER_SUMMARY = "Click 'Generate AI Summary' to analyze"
PLACEHOLDER_PROMPT = "(not yet generated)"
AVAILABLE_METRICS = [
    "heart_rate",
    "calories_per_min",
    "speed_kph",
    "power",
    "cadence"
]
NORM_BOUNDS = {
    "heart_rate": (50, 200),
    "calories_per_min": (0, 20),
    "speed_kph": (0, 15),
    "power": (0, 400),
    "cadence": (0, 120),
}

# PyTorch Model Configuration
LATENT_DIM = 64  # Must match the trained autoencoder
MODEL_PATH_ENV = "WORKOUT_ENCODER_MODEL_PATH"  # Environment variable for model path
DEFAULT_MODEL_PATH = "workout_encoder.pth"  # Default relative to experiment directory


@ray.remote
class ExperimentActor:
    """
    This is the "Headless Server" for data_imaging_advanced.
    It uses a PyTorch autoencoder to generate 64-dimensional embeddings
    instead of the fixed 8x8x3 flattening approach.
    """

    def __init__(self, mongo_uri: str, db_name: str, write_scope: str, read_scopes: list[str]):
        self.write_scope = write_scope
        self.read_scopes = read_scopes
        
        # Critical fix: Ensure write_scope is in read_scopes so we can read what we write!
        if write_scope not in read_scopes:
            logger.warning(f"[{write_scope}-Actor] ⚠️ WARNING: write_scope '{write_scope}' not in read_scopes {read_scopes}! "
                         f"Adding it to prevent data visibility issues.")
            self.read_scopes = list(read_scopes) + [write_scope]
        
        self.vector_index_name = f"{write_scope}_workout_vector_index"
        
        # Lazy-load heavy dependencies (experiment-specific)
        try:
            import httpx
            import numpy
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot
            from PIL import Image
            from fastapi.templating import Jinja2Templates
            from pymongo.errors import OperationFailure
            
            self.httpx = httpx
            self.np = numpy
            self.plt = matplotlib.pyplot
            self.Image = Image
            self.OperationFailure = OperationFailure
            
            if templates_dir.is_dir():
                self.templates = Jinja2Templates(directory=str(templates_dir))
            else:
                self.templates = None
                logger.warning(f"[{write_scope}-Actor] Template dir not found at {templates_dir}")
            
            logger.info(f"[{write_scope}-Actor] Successfully loaded heavy dependencies.")
        except ImportError as e:
            logger.critical(f"[{write_scope}-Actor] ❌ CRITICAL: Failed to load dependencies: {e}", exc_info=True)
            self.httpx = None
            self.np = None
            self.plt = None
            self.Image = None
            self.OperationFailure = None
            self.templates = None
        
        # --- PyTorch Model Loading ---
        self.encoder_model = None
        self.torch = None
        self.model_path_used = None
        try:
            import torch
            import torch.nn as nn
            self.torch = torch
            self.nn = nn
            
            # Determine model path - use hardcoded absolute path in Docker container
            # The model file is copied into the Docker image at /app/experiments/data_imaging_advanced/workout_encoder.pth
            model_path = "/app/experiments/data_imaging_advanced/workout_encoder.pth"
            
            logger.info(f"[{write_scope}-Actor] 🔍 Looking for model at: {model_path}")
            logger.info(f"[{write_scope}-Actor]   - File exists: {os.path.exists(model_path)}")
            
            # Fallback: try experiment_dir if hardcoded path doesn't work
            if not os.path.exists(model_path):
                fallback_path = str(experiment_dir / DEFAULT_MODEL_PATH)
                logger.warning(f"[{write_scope}-Actor] ⚠️ Hardcoded path not found, trying fallback: {fallback_path}")
                if os.path.exists(fallback_path):
                    model_path = fallback_path
                    logger.info(f"[{write_scope}-Actor] ✅ Using fallback path: {model_path}")
            
            if not os.path.exists(model_path):
                logger.error(f"[{write_scope}-Actor] ❌ Model file not found at {model_path}")
                logger.error(f"[{write_scope}-Actor] Please generate the model offline before running:")
                logger.error(f"[{write_scope}-Actor]   python experiments/data_imaging_advanced/train_model.py --output-path {model_path}")
                logger.error(f"[{write_scope}-Actor] The model should be generated outside Docker and included in the image.")
                self.encoder_model = None
            else:
                logger.info(f"[{write_scope}-Actor] Loading PyTorch encoder model from: {model_path}")
                device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
                logger.info(f"[{write_scope}-Actor] Using device: {device}")
                
                try:
                    # Define the autoencoder architecture
                    logger.info(f"[{write_scope}-Actor] Creating autoencoder model architecture...")
                    autoencoder_model = self._create_autoencoder_model()
                    # Extract just the encoder part
                    encoder_to_load = autoencoder_model.encoder
                    logger.info(f"[{write_scope}-Actor] Encoder architecture created.")
                    
                    # Load the saved state_dict
                    logger.info(f"[{write_scope}-Actor] Loading state_dict from {model_path}...")
                    try:
                        state_dict = torch.load(model_path, map_location=device)
                        logger.info(f"[{write_scope}-Actor] State dict loaded. Keys: {list(state_dict.keys())[:5]}... (showing first 5)")
                    except Exception as load_err:
                        logger.critical(f"[{write_scope}-Actor] ❌ Failed to torch.load() the file: {load_err}", exc_info=True)
                        raise
                    
                    try:
                        encoder_to_load.load_state_dict(state_dict)
                        logger.info(f"[{write_scope}-Actor] State dict loaded into encoder.")
                    except Exception as load_err:
                        logger.critical(f"[{write_scope}-Actor] ❌ Failed to load_state_dict(): {load_err}", exc_info=True)
                        logger.critical(f"[{write_scope}-Actor] Expected keys: {list(encoder_to_load.state_dict().keys())[:5]}")
                        logger.critical(f"[{write_scope}-Actor] File keys: {list(state_dict.keys())[:5]}")
                        raise
                    
                    encoder_to_load.to(device)
                    encoder_to_load.eval()
                    
                    self.encoder_model = encoder_to_load
                    self.device = device
                    self.model_path_used = model_path
                    logger.info(f"[{write_scope}-Actor] ✅ PyTorch encoder model loaded successfully on {device} and set to eval mode.")
                    
                    # Test the model with dummy input to verify it works
                    try:
                        test_input = torch.randn(1, 3, 64).to(device)
                        with torch.no_grad():
                            test_output = encoder_to_load(test_input)
                        logger.info(f"[{write_scope}-Actor] ✅ Model test successful. Output shape: {test_output.shape}, Output range: [{test_output.min().item():.4f}, {test_output.max().item():.4f}]")
                        if torch.allclose(test_output, torch.zeros_like(test_output)):
                            logger.warning(f"[{write_scope}-Actor] ⚠️ WARNING: Model test returned all zeros! Model may not be working correctly.")
                    except Exception as test_e:
                        logger.warning(f"[{write_scope}-Actor] ⚠️ Model test failed: {test_e}", exc_info=True)
                        
                except Exception as load_e:
                    logger.critical(f"[{write_scope}-Actor] ❌ CRITICAL: Failed to load model: {load_e}", exc_info=True)
                    import traceback
                    logger.critical(f"[{write_scope}-Actor] Full traceback:\n{traceback.format_exc()}")
                    self.encoder_model = None
        except ImportError:
            logger.warning(f"[{write_scope}-Actor] PyTorch not installed. Embedding generation will fail.")
            self.encoder_model = None
            self.torch = None
            self.nn = None
        except Exception as e:
            logger.critical(f"[{write_scope}-Actor] ❌ CRITICAL: Failed to load PyTorch model: {e}", exc_info=True)
            self.encoder_model = None
        
        # --- VoyageAI Client Setup ---
        VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")
        if not VOYAGE_API_KEY:
            logger.warning(f"[{write_scope}-Actor] VOYAGE_API_KEY not set. Reranking features will be disabled.")
            self.voyage_client = None
        else:
            try:
                import voyageai
                self.voyage_client = voyageai.AsyncClient(api_key=VOYAGE_API_KEY)
                self.VOYAGE_RERANK_MODEL = "rerank-2.5-lite"
                logger.info(f"[{write_scope}-Actor] VoyageAI client initialized successfully.")
            except ImportError:
                logger.warning(f"[{write_scope}-Actor] voyageai library not installed. Reranking features will be disabled.")
                self.voyage_client = None
                self.VOYAGE_RERANK_MODEL = None
            except Exception as e:
                logger.error(f"[{write_scope}-Actor] Failed to initialize VoyageAI client: {e}", exc_info=True)
                self.voyage_client = None
                self.VOYAGE_RERANK_MODEL = None
        
        # Database initialization
        try:
            from experiment_db import create_actor_database
            self.db = create_actor_database(
                mongo_uri,
                db_name,
                write_scope,
                self.read_scopes
            )
            logger.info(
                f"[{write_scope}-Actor] initialized with write_scope='{self.write_scope}' "
                f"(DB='{db_name}') using magical database abstraction"
            )
        except Exception as e:
            logger.critical(f"[{write_scope}-Actor] ❌ CRITICAL: Failed to init DB: {e}")
            self.db = None

    # ============================================================================
    # PyTorch Model Definition
    # ============================================================================

    def _create_autoencoder_model(self):
        """Creates the autoencoder model architecture matching the training script."""
        # Capture nn module from actor to use in class definition
        nn = self.nn
        
        class Autoencoder(nn.Module):
            def __init__(self, latent_dim=LATENT_DIM):
                super(Autoencoder, self).__init__()
                
                # Encoder
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
                
                # Decoder (not used for inference, but part of class)
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
                    nn.Sigmoid()
                )
            
            def forward(self, x):
                x = self.encoder(x)
                x = self.decoder(x)
                return x
        
        return Autoencoder(latent_dim=LATENT_DIM)

    # ============================================================================
    # Model Preprocessing Functions
    # ============================================================================

    def _normalize_for_model(self, data, min_val: float, max_val: float):
        """
        Clips and normalizes a NumPy array to a 0.0 - 1.0 float scale.
        This matches the preprocessing used during training.
        """
        clipped_data = self.np.clip(data, min_val, max_val)
        range_val = max_val - min_val
        if range_val == 0:
            return self.np.zeros_like(clipped_data, dtype=self.np.float32)
        normalized = (clipped_data - min_val) / range_val
        return normalized.astype(self.np.float32)

    def _preprocess_for_model(self, doc: dict):
        """
        Takes a single workout doc, validates, normalizes [0,1],
        and stacks it into the (3, 64) model input shape for PyTorch.
        Returns None if data is invalid.
        """
        try:
            ts = doc['time_series']
            hr_data = self.np.array(ts['heart_rate'])
            cal_data = self.np.array(ts['calories_per_min'])
            speed_data = self.np.array(ts['speed_kph'])
            
            if not (len(hr_data) == 64 and len(cal_data) == 64 and len(speed_data) == 64):
                return None
            
            hr_norm = self._normalize_for_model(hr_data, *NORM_BOUNDS["heart_rate"])
            cal_norm = self._normalize_for_model(cal_data, *NORM_BOUNDS["calories_per_min"])
            speed_norm = self._normalize_for_model(speed_data, *NORM_BOUNDS["speed_kph"])
            
            # PyTorch Conv1d expects (Batch, Channels, Length)
            # So we want our input to be (3, 64)
            model_input = self.np.stack([hr_norm, cal_norm, speed_norm], axis=0)
            return model_input.astype(self.np.float32)
        
        except Exception as e:
            logger.error(f"Error in _preprocess_for_model: {e}")
            return None

    # ============================================================================
    # Embedding Generation (PyTorch-based)
    # ============================================================================

    def _get_feature_vector(self, doc: dict):
        """
        Main "embedding" function using PyTorch encoder.
        Takes a workout doc, runs it through the loaded PyTorch encoder,
        and returns the 1D (64-element) latent vector.
        """
        if self.encoder_model is None or self.torch is None:
            logger.error(f"[{self.write_scope}-Actor] CRITICAL: Encoder model is not loaded. Returning zero vector.")
            logger.error(f"[{self.write_scope}-Actor] encoder_model={self.encoder_model}, torch={self.torch}")
            return self.np.zeros(LATENT_DIM, dtype=self.np.float32)
        
        try:
            # 1. Preprocess the doc into the (3, 64) tensor format
            model_input_np = self._preprocess_for_model(doc)
            
            if model_input_np is None:
                logger.warning(f"[{self.write_scope}-Actor] Invalid data for doc {doc.get('_id')}. Returning zero vector.")
                return self.np.zeros(LATENT_DIM, dtype=self.np.float32)
            
            # 2. Convert to PyTorch Tensor
            # .unsqueeze(0) adds the batch dimension -> (1, 3, 64)
            model_input_tensor = self.torch.from_numpy(model_input_np).unsqueeze(0).to(self.device)
            
            # 3. Run inference
            with self.torch.no_grad():  # CRITICAL: Disables gradient calculation
                latent_vector_tensor = self.encoder_model(model_input_tensor)
            
            # 4. Convert back to NumPy array
            # .squeeze(0) removes the batch dimension -> (64,)
            latent_vector_np = latent_vector_tensor.squeeze(0).cpu().numpy()
            
            # Validate output
            if self.np.allclose(latent_vector_np, 0.0):
                logger.warning(f"[{self.write_scope}-Actor] ⚠️ WARNING: Encoder returned all zeros! This may indicate a model issue.")
                logger.warning(f"[{self.write_scope}-Actor] Input shape: {model_input_np.shape}, Input range: [{model_input_np.min():.4f}, {model_input_np.max():.4f}]")
            
            return latent_vector_np
            
        except Exception as e:
            logger.error(f"[{self.write_scope}-Actor] ❌ ERROR in _get_feature_vector: {e}", exc_info=True)
            logger.error(f"[{self.write_scope}-Actor] Returning zero vector due to error.")
            return self.np.zeros(LATENT_DIM, dtype=self.np.float32)

    def _get_hardcoded_feature_vector(self, doc: dict):
        """
        Generates the hardcoded 192-dim vector (8x8x3 flattening) for comparison.
        This matches the method used in data_imaging experiment.
        """
        arrays = self._generate_workout_viz_arrays(
            doc, 
            size=8,
            r_key="heart_rate",
            g_key="calories_per_min",
            b_key="speed_kph",
            alpha_mode="none"
        )
        # Flatten the 8x8x3 RGB array to 192-dim vector
        return arrays["rgb_combined"].reshape(-1).astype(self.np.float32)

    # ============================================================================
    # Visualization Functions (Unchanged from data_imaging)
    # ============================================================================

    async def _call_openai_api(self, prompt: str) -> str:
        """Calls the OpenAI Chat Completion endpoint."""
        OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
        if not OPENAI_API_KEY:
            logger.error("OpenAI API key not set (OPENAI_API_KEY). Returning error message.")
            return "ERROR: OpenAI API key (OPENAI_API_KEY) is not set in the server environment."

        system_prompt = (
            "You are a professional Workout Radiologist. Your job is to synthesize the provided data "
            "into a concise, qualitative summary (max 3 sentences)."
        )
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 100,
        }
        
        try:
            async with self.httpx.AsyncClient(timeout=30) as client:
                resp = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except self.httpx.HTTPStatusError as e:
            logger.error(f"OpenAI API error: {e.response.status_code} - {e.response.text}")
            return f"ERROR: OpenAI API returned status {e.response.status_code}. Check server logs."
        except Exception as e:
            logger.error(f"Error calling OpenAI API: {e}")
            return f"ERROR: Could not connect to OpenAI API. Details: {e}"

    def _analyze_time_series_features(self, doc: dict, neighbors: list[dict]) -> tuple[str, str]:
        """Computes a classification label and a prompt for the doc."""
        try:
            hr = self.np.array(doc["time_series"]["heart_rate"], dtype=float)
            cal = self.np.array(doc["time_series"]["calories_per_min"], dtype=float)
            spd = self.np.array(doc["time_series"]["speed_kph"], dtype=float)
        except (KeyError, TypeError):
            return ("Malformed Data", PLACEHOLDER_PROMPT)

        hr_avg = float(self.np.mean(hr))
        hr_max = float(self.np.max(hr))
        cal_sum = float(self.np.sum(cal))
        spd_std = float(self.np.std(spd))

        # Simple classification logic
        if spd_std > 2.5 and hr_max > 180:
            classification = "High Intensity Interval"
        elif spd_std < 1.0 and hr_avg > 130:
            classification = "Steady Aerobic"
        else:
            classification = "Mixed/Variable"

        # Summarize neighbors for the prompt
        lines = []
        if neighbors:
            for i, n in enumerate(neighbors):
                sid = n["_id"].split("_")[-1]
                score = n.get("score", 0)
                wtype = n.get("workout_type", "?")
                lines.append(f"- Neighbor {i+1}: id=#{sid}, Score={score:.4f}, Type={wtype}")
        else:
            lines.append("- No neighbors found or only one doc in DB.")

        neighbor_text = "\n".join(lines)
        prompt = f"""Workout ID: {doc.get('_id','?')}
[Time-Series Stats]
- HR Avg={hr_avg:.1f}, HR Max={hr_max:.1f}
- Total Calories={cal_sum:.1f}
- Speed StdDev={spd_std:.2f}

[Neighbors]
{neighbor_text}

**Please provide a final radiologist summary (<=3 sentences) highlighting anomalies or conflicts.**
"""
        return classification, prompt

    def _create_synthetic_apple_watch_data(self, suffix: int) -> dict:
        """Generates synthetic data with random variations."""
        self.np.random.seed(suffix)
        t = self.np.linspace(0, 2 * self.np.pi, 64)

        hr_base = 100 + (suffix % 7)*5
        cal_base = 5 + (suffix % 5)*1
        speed_base = 3.5 + (suffix % 6)*0.5

        hr_array = hr_base + 60*self.np.sin(t + self.np.random.rand()*0.5) + self.np.random.rand(64)*10
        hr_array[:5] *= 0.8
        hr_array[-5:] *= 0.9

        cal_array = cal_base + 4*self.np.sin(t + self.np.random.rand()*0.3) + self.np.random.rand(64)*2

        spd_array = self.np.full(64, speed_base) + self.np.random.rand(64)*0.4
        spd_array[:5] = 2.0 + self.np.random.rand(5)*0.4
        spd_array[-5:] = 1.2 + self.np.random.rand(5)*0.3

        power_base = 150 + (suffix % 8) * 10
        power_array = power_base + 50 * self.np.sin(t + self.np.random.rand() * 0.7) + self.np.random.rand(64) * 15
        power_array[power_array < 0] = 0

        cadence_base = 80 + (suffix % 4) * 5
        cadence_array = self.np.full(64, cadence_base) + self.np.random.rand(64) * 3
        cadence_array[10:15] = 0 
        cadence_array[40:45] = 0

        # Generate data quality array
        data_quality = self.np.full(64, 255, dtype=self.np.uint8)
        dropout_indices = self.np.random.choice(64, size=min(5, 64), replace=False)
        data_quality[dropout_indices] = 0
        data_quality[10:15] = 0
        data_quality[40:45] = 0

        # Generate session tag and RPE
        intensity_pattern = suffix % 10
        if intensity_pattern in [0, 1, 2, 7, 8]:
            session_tag = str(self.np.random.choice(["Tempo Pace", "Threshold", "Race Day", "High Intensity Interval"]))
            rpe = float(self.np.random.randint(7, 10))
        elif intensity_pattern in [5, 6]:
            session_tag = str(self.np.random.choice(["Recovery", "Z2 Cardio", "Easy Recovery Run"]))
            rpe = float(self.np.random.randint(2, 5))
        else:
            session_tag = str(self.np.random.choice(["Race Day", "Recovery", "Z2 Cardio", "Tempo Pace", "Threshold"]))
            rpe = float(self.np.random.randint(4, 8))

        doc_id = f"workout_rad_{suffix}"
        return {
            "_id": doc_id,
            "time_series": {
                "heart_rate": self.np.round(self.np.maximum(hr_array, NORM_BOUNDS["heart_rate"][0]), 2).tolist(),
                "calories_per_min": self.np.round(self.np.maximum(cal_array, NORM_BOUNDS["calories_per_min"][0]), 2).tolist(),
                "speed_kph": self.np.round(self.np.maximum(spd_array, NORM_BOUNDS["speed_kph"][0]), 2).tolist(),
                "power": self.np.round(self.np.maximum(power_array, NORM_BOUNDS["power"][0]), 2).tolist(),
                "cadence": self.np.round(self.np.maximum(cadence_array, NORM_BOUNDS["cadence"][0]), 2).tolist(),
            },
            "data_quality": data_quality.tolist(),
            "rpe": rpe,
            "start_time": datetime(2025, 10, 27, 10, 10 + (suffix % 40), 0, tzinfo=timezone.utc),
            "workout_type": str(self.np.random.choice(["Outdoor Run", "Cycling", "Strength", "Yoga"])),
            "session_tag": session_tag,
            "post_session_notes": {
                "hydration_ml": int(self.np.random.randint(500, 2500)),
                "notes": str(self.np.random.choice(["Felt good", "Legs sore", "Pushed harder", "Casual run"])),
            },
            "gear_used": [
                {"item": "shoes_v3", "kilometers": float(self.np.random.randint(50, 200))},
                {"item": "hrm_strap", "battery_life_percent": int(self.np.random.randint(10, 100))},
            ],
            "ai_classification": PLACEHOLDER_CLASSIFICATION,
            "ai_summary": PLACEHOLDER_SUMMARY,
            "llm_analysis_prompt": PLACEHOLDER_PROMPT,
        }

    def _norm_array(self, x, lo, hi):
        """Clips and normalizes a NumPy array to 0-255 uint8."""
        x_clipped = self.np.clip(x, lo, hi)
        rng = hi - lo
        if rng <= 0:
            return self.np.zeros_like(x_clipped, dtype=self.np.uint8)
        return ((x_clipped - lo) / rng * 255).astype(self.np.uint8)

    def _generate_workout_viz_arrays(
        self, 
        doc: dict, 
        size=8, 
        r_key: str = "heart_rate", 
        g_key: str = "calories_per_min", 
        b_key: str = "speed_kph",
        alpha_mode: str = "none",
        alpha_key: str = "cadence"
    ):
        """
        Generates the normalized 8x8x3 RGB or 8x8x4 RGBA array plus raw arrays.
        This is for visualization only, separate from the embedding generation.
        """
        
        def get_raw_data(key):
            return doc.get("time_series", {}).get(key, [0]*64)

        raw_data = {key: self.np.array(get_raw_data(key), dtype=float) for key in AVAILABLE_METRICS}
        
        r_bounds = NORM_BOUNDS.get(r_key, (0, 1))
        g_bounds = NORM_BOUNDS.get(g_key, (0, 1))
        b_bounds = NORM_BOUNDS.get(b_key, (0, 1))

        r_1d = self._norm_array(raw_data.get(r_key, self.np.zeros(64)), *r_bounds)
        g_1d = self._norm_array(raw_data.get(g_key, self.np.zeros(64)), *g_bounds)
        b_1d = self._norm_array(raw_data.get(b_key, self.np.zeros(64)), *b_bounds)

        r_2d = r_1d.reshape(size, size)
        g_2d = g_1d.reshape(size, size)
        b_2d = b_1d.reshape(size, size)

        # Generate alpha channel based on mode
        alpha_2d = None
        alpha_key_used = None
        if alpha_mode == "fourth_metric":
            alpha_bounds = NORM_BOUNDS.get(alpha_key, (0, 1))
            alpha_data = self.np.array(get_raw_data(alpha_key), dtype=float)
            alpha_1d = self._norm_array(alpha_data, *alpha_bounds)
            alpha_2d = alpha_1d.reshape(size, size)
            alpha_key_used = alpha_key
        elif alpha_mode == "data_quality":
            data_quality = doc.get("data_quality", [255]*64)
            alpha_1d = self.np.array(data_quality, dtype=self.np.uint8)
            alpha_2d = alpha_1d.reshape(size, size)
            alpha_key_used = "data_quality"
        elif alpha_mode == "global_rpe":
            rpe = doc.get("rpe", 5.0)
            rpe_normalized = int(self.np.clip((rpe - 1) / 9 * 255, 0, 255))
            alpha_2d = self.np.full((size, size), rpe_normalized, dtype=self.np.uint8)
            alpha_key_used = "rpe"
        else:
            alpha_mode = "none"

        # Stack channels
        if alpha_mode != "none" and alpha_2d is not None:
            rgba = self.np.stack([r_2d, g_2d, b_2d, alpha_2d], axis=-1)
            combined = rgba
        else:
            rgb = self.np.stack([r_2d, g_2d, b_2d], axis=-1)
            combined = rgb
        
        result = {
            "rgb_combined": combined,
            "raw_data": raw_data, 
            "channel_r_2d": r_2d,
            "channel_g_2d": g_2d,
            "channel_b_2d": b_2d,
            "selected_keys": {"r": r_key, "g": g_key, "b": b_key},
            "selected_bounds": {"r": r_bounds, "g": g_bounds, "b": b_bounds},
            "alpha_mode": alpha_mode,
            "alpha_key": alpha_key_used
        }
        
        if alpha_2d is not None:
            result["channel_a_2d"] = alpha_2d
        
        return result

    def _encode_png_b64(
        self,
        img_array,
        size=(128, 128),
        tint_color=None
    ) -> str:
        """Encodes a NumPy array to a Base64 PNG. Supports RGB, RGBA, and grayscale."""
        error_placeholder = (
            "iVBORw0KGgoAAAANSUEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42"
            "mNkYAAAAYAAjCB0C8AAAAASUVORK5CYII="
        )
        try:
            if tint_color is not None and img_array.ndim == 2:
                colored_array = self.np.zeros((*img_array.shape, 3), dtype=self.np.uint8)
                for i in range(3):
                    if tint_color[i] > 0:
                        colored_array[..., i] = (
                            img_array.astype(float) * tint_color[i] / 255
                        ).astype(self.np.uint8)
                img = self.Image.fromarray(colored_array, "RGB")
            elif img_array.ndim == 2:
                img = self.Image.fromarray(img_array, "L")
            elif img_array.shape[-1] == 4:
                img = self.Image.fromarray(img_array, "RGBA")
            elif img_array.shape[-1] == 3:
                img = self.Image.fromarray(img_array, "RGB")
            else:
                img = self.Image.fromarray(img_array, "RGB")

            if size:
                img = img.resize(size, self.Image.NEAREST)

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception as e:
            logger.error(f"Image encoding failed: {e}")
            return error_placeholder

    def _generate_chart_base64(self, data, color="#FF6868") -> str:
        """Generates a Matplotlib line chart from a 1D list or array, returns base64 PNG."""
        arr = self.np.array(data, dtype=float)
        
        self.plt.style.use("dark_background")
        fig, ax = self.plt.subplots(figsize=(4.5, 2.5), dpi=100)
        fig.patch.set_facecolor("#132A38")
        ax.set_facecolor("#132A38")

        ax.plot(arr, color=color, linewidth=2)
        ax.set_xlim(0, len(arr)-1 if len(arr) > 1 else 1)
        ax.tick_params(axis="x", colors="#A7B6C2")
        ax.tick_params(axis="y", colors="#A7B6C2")
        ax.set_xticks([0, len(arr)-1])
        ax.set_xticklabels(["Start", "End"])
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["bottom"].set_color("#23435B")
        ax.spines["left"].set_color("#23435B")

        buf = io.BytesIO()
        try:
            fig.savefig(buf, format="PNG", facecolor=fig.get_facecolor(), bbox_inches='tight', pad_inches=0.1)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception as e:
            logger.error(f"Chart generation error: {e}")
            return ""
        finally:
            self.plt.close(fig)
            self.plt.style.use("default")

    def _convert_numpy_types(self, obj):
        """Recursively converts NumPy types to native Python types for MongoDB compatibility."""
        if isinstance(obj, dict):
            return {key: self._convert_numpy_types(value) for key, value in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._convert_numpy_types(item) for item in obj]
        elif hasattr(obj, 'tolist'):
            return obj.tolist()
        elif hasattr(obj, 'item'):
            try:
                return obj.item()
            except (ValueError, AttributeError):
                pass
        
        obj_type = type(obj)
        type_module = getattr(obj_type, '__module__', '')
        if type_module and 'numpy' in type_module:
            type_name = obj_type.__name__
            if 'float' in type_name:
                return float(obj)
            elif 'int' in type_name or 'uint' in type_name:
                return int(obj)
            elif 'str' in type_name or 'unicode' in type_name:
                return str(obj)
            elif 'bool' in type_name:
                return bool(obj)
            elif hasattr(obj, 'item'):
                return obj.item()
        
        return obj

    # ============================================================================
    # Public API Methods
    # ============================================================================

    async def initialize(self):
        """Post-initialization hook: waits for vector index to be ready, then ensures at least ~100 records exist."""
        if not self.db:
            logger.warning(f"[{self.write_scope}-Actor] Skipping initialize - DB not ready.")
            return
        
        logger.info(f"[{self.write_scope}-Actor] Starting post-initialization setup...")
        
        # Wait for vector search index to be ready
        logger.info(f"[{self.write_scope}-Actor] Waiting for vector search index '{self.vector_index_name}' to be ready...")
        await asyncio.sleep(3)
        
        from async_mongo_wrapper import AsyncAtlasIndexManager
        index_manager = AsyncAtlasIndexManager(self.db.database[self.write_scope + "_workouts"])
        max_wait = 30
        wait_interval = 2
        waited = 0
        
        while waited < max_wait:
            try:
                index_info = await index_manager.get_search_index(self.vector_index_name)
                if index_info and index_info.get("queryable"):
                    logger.info(f"[{self.write_scope}-Actor] Vector search index '{self.vector_index_name}' is ready!")
                    break
                elif index_info and index_info.get("status") == "FAILED":
                    logger.error(f"[{self.write_scope}-Actor] Vector search index '{self.vector_index_name}' is in FAILED state!")
                    break
                else:
                    logger.debug(f"[{self.write_scope}-Actor] Index '{self.vector_index_name}' not ready yet, waiting...")
            except Exception as e:
                logger.debug(f"[{self.write_scope}-Actor] Error checking index status: {e}")
            
            await asyncio.sleep(wait_interval)
            waited += wait_interval
        
        if waited >= max_wait:
            logger.warning(f"[{self.write_scope}-Actor] Timeout waiting for index, but continuing...")
        
        try:
            logger.info(f"[{self.write_scope}-Actor] Verifying database connection and collection access...")
            logger.info(f"[{self.write_scope}-Actor] write_scope='{self.write_scope}', read_scopes={self.read_scopes}")
            
            try:
                test_query = await self.db.workouts.find_one({}, {"_id": 1})
                logger.info(f"[{self.write_scope}-Actor] Database connection verified. Test query returned: {test_query is not None}")
            except Exception as test_e:
                logger.error(f"[{self.write_scope}-Actor] Database connection test failed: {test_e}", exc_info=True)
                raise
            
            logger.info(f"[{self.write_scope}-Actor] Counting existing workout records (scoped to experiment_id in {self.read_scopes})...")
            count = await self.db.workouts.count_documents({})
            logger.info(f"[{self.write_scope}-Actor] Found {count} existing workout records.")
            
            if count == 0:
                sample_docs = await self.db.workouts.find({}, {"_id": 1}).limit(1).to_list(length=1)
                if sample_docs:
                    logger.warning(f"[{self.write_scope}-Actor] ⚠️ WARNING: count_documents returned 0, but find() found documents!")
                    return
                
                logger.info(f"[{self.write_scope}-Actor] Verified: No records found. Generating ~100 sample workout records...")
                NUM_TO_GENERATE = 100
                generated_ids = []
                
                BATCH_SIZE = 25
                for batch_start in range(0, NUM_TO_GENERATE, BATCH_SIZE):
                    batch_end = min(batch_start + BATCH_SIZE, NUM_TO_GENERATE)
                    batch_size = batch_end - batch_start
                    logger.info(f"[{self.write_scope}-Actor] Generating batch {batch_start // BATCH_SIZE + 1}: workouts {batch_start} to {batch_end - 1}")
                    
                    for i in range(batch_size):
                        try:
                            new_id = await self.generate_one()
                            generated_ids.append(new_id)
                            await asyncio.sleep(0.1)
                        except Exception as e:
                            logger.error(f"[{self.write_scope}-Actor] Error generating workout {batch_start + i + 1}/{NUM_TO_GENERATE}: {e}")
                    
                    if batch_end < NUM_TO_GENERATE:
                        await asyncio.sleep(0.5)
                
                logger.info(f"[{self.write_scope}-Actor] Successfully generated {len(generated_ids)} workout records: {generated_ids}")
            else:
                logger.info(f"[{self.write_scope}-Actor] Records already exist (count={count}). Skipping auto-generation.")
                
        except Exception as e:
            logger.error(f"[{self.write_scope}-Actor] Error during initialization: {e}", exc_info=True)
        
        logger.info(f"[{self.write_scope}-Actor] Post-initialization setup complete.")

    def _check_ready(self):
        """Check if actor is ready."""
        if not self.db:
            raise RuntimeError("Database not initialized. Check logs for import errors.")
        if not self.templates:
            raise RuntimeError("Templates not loaded. Check logs for import errors.")
        if not self.np or not self.plt or not self.httpx or not self.Image:
            raise RuntimeError("Heavy dependencies not loaded. Check logs for import errors.")
        if not self.encoder_model:
            logger.warning("PyTorch encoder model not loaded. Embedding generation will fail.")

    async def generate_one(self) -> int:
        """Generates a new workout document with PyTorch encoder embedding."""
        self._check_ready()

        pipeline = [
            {"$match": {"_id": {"$regex": "^workout_rad_\\d+$"}}},
            {"$project": {"num": {"$toInt": {"$arrayElemAt": [{"$split": ["$_id","_"]}, -1]}}}},
            {"$group": {"_id": None, "max_id": {"$max":"$num"}}},
        ]
        result_list = await self.db.raw.workouts.aggregate(pipeline).to_list(1)
        max_id = result_list[0]["max_id"] if result_list and 'max_id' in result_list[0] else -1
        new_suffix = max_id + 1

        for attempt in range(5):
            doc = self._create_synthetic_apple_watch_data(new_suffix)
            
            # Generate 64-dim vector using PyTorch encoder
            feature_vec = self._get_feature_vector(doc)
            doc["workout_vector"] = feature_vec.tolist()
            
            try:
                # Convert all NumPy types to native Python types before insertion
                doc = self._convert_numpy_types(doc)
                await self.db.workouts.insert_one(doc)
                logger.info(f"[{self.write_scope}-Actor] Inserted new doc {doc['_id']} with PyTorch encoder vector (64-dim)")
                return new_suffix
            except Exception as e:
                if "duplicate" in str(e).lower() or "E11000" in str(e):
                    logger.warning(f"[{self.write_scope}-Actor] Collision on doc {doc['_id']}. Retrying...")
                    new_suffix += 1
                else:
                    raise
        
        logger.error(f"[{self.write_scope}-Actor] Could not generate new doc after 5 collisions.")
        raise Exception("Actor could not generate new doc after multiple collisions.")

    async def clear_all(self) -> dict:
        """Clears all documents in the scoped collection."""
        self._check_ready()
        result = await self.db.workouts.delete_many({})
        deleted_count = result.deleted_count
        logger.info(f"[{self.write_scope}-Actor] Cleared {deleted_count} documents.")
        return {"deleted_count": deleted_count}

    def _get_doc_as_rerank_string(self, doc: Dict[str, Any]) -> str:
        """Converts a workout document into a concise string for VoyageAI Reranker."""
        workout_type = doc.get('workout_type', 'N/A')
        session_tag = doc.get('session_tag', 'N/A')
        notes = doc.get('post_session_notes', {}).get('notes', 'N/A')
        classification = doc.get('ai_classification', 'N/A')
        
        if classification == PLACEHOLDER_CLASSIFICATION:
            classification = "Unclassified"
            
        return (
            f"Workout Type: {workout_type}. "
            f"User Tag: {session_tag}. "
            f"User Notes: {notes}. "
            f"AI Classification: {classification}."
        )

    async def _generate_viz_data(
        self, 
        doc: dict, 
        r_key: str, 
        g_key: str, 
        b_key: str,
        alpha_mode: str = "none",
        alpha_key: str = "cadence"
    ) -> dict:
        """Generates all B64 images and labels for the selected keys."""
        self._check_ready()
        
        arrays = self._generate_workout_viz_arrays(
            doc, 
            size=8,
            r_key=r_key,
            g_key=g_key,
            b_key=b_key,
            alpha_mode=alpha_mode,
            alpha_key=alpha_key
        )
        
        b64_combined = self._encode_png_b64(arrays["rgb_combined"], (256,256))
        b64_r = self._encode_png_b64(arrays["channel_r_2d"], (128,128), tint_color=(255,0,0))
        b64_g = self._encode_png_b64(arrays["channel_g_2d"], (128,128), tint_color=(0,255,0))
        b64_b = self._encode_png_b64(arrays["channel_b_2d"], (128,128), tint_color=(0,0,255))
        
        # Generate alpha channel visualization if present
        b64_a = None
        label_a_full_html = None
        label_a_short_html = None
        if arrays.get("channel_a_2d") is not None:
            b64_a = self._encode_png_b64(arrays["channel_a_2d"], (128,128), tint_color=(255,255,255))
            
            alpha_key_used = arrays.get("alpha_key", "unknown")
            if alpha_mode == "fourth_metric":
                title = alpha_key_used.replace('_', ' ').title()
                bounds = NORM_BOUNDS.get(alpha_key_used, ['?','?'])
                label_a_full_html = f"<b>A:</b> {title} ({bounds[0]}-{bounds[1]})"
                label_a_short_html = f"<strong>{title}</strong> data provides the pixel values for the <strong class=\"alpha-label\">Alpha channel</strong>."
            elif alpha_mode == "data_quality":
                label_a_full_html = "<b>A:</b> Data Quality (255=Good, 0=Bad/Missing)"
                label_a_short_html = "<strong>Data Quality</strong> (255=Opaque/Good, 0=Transparent/Bad) provides the pixel values for the <strong class=\"alpha-label\">Alpha channel</strong>."
            elif alpha_mode == "global_rpe":
                rpe = doc.get("rpe", 5.0)
                label_a_full_html = f"<b>A:</b> RPE (Rated Perceived Exertion) = {rpe:.1f}/10"
                label_a_short_html = f"<strong>RPE</strong> ({rpe:.1f}/10) provides a global alpha value for the <strong class=\"alpha-label\">Alpha channel</strong> (same for all pixels)."

        def format_label(key_char: str, key: str) -> str:
            title = key.replace('_', ' ').title()
            bounds = NORM_BOUNDS.get(key, ['?','?'])
            return f"<b>{key_char.upper()}:</b> {title} ({bounds[0]}-{bounds[1]})"
            
        def format_short_label(key: str, color_class: str, channel_name: str) -> str:
            title = key.replace('_', ' ').title()
            return f"<strong>{title}</strong> data provides the pixel values for the <strong class=\"{color_class}\">{channel_name} channel</strong>."

        raw_data_serializable = {
            key: arr.tolist() if hasattr(arr, 'tolist') else arr 
            for key, arr in arrays["raw_data"].items()
        }
        
        result = {
            "b64_combined": b64_combined,
            "b64_r": b64_r,
            "b64_g": b64_g,
            "b64_b": b64_b,
            "label_r_full_html": format_label("r", r_key),
            "label_g_full_html": format_label("g", g_key),
            "label_b_full_html": format_label("b", b_key),
            "label_r_short_html": format_short_label(r_key, "red-label", "Red"),
            "label_g_short_html": format_short_label(g_key, "green-label", "Green"),
            "label_b_short_html": format_short_label(b_key, "blue-label", "Blue"),
            "raw_data": raw_data_serializable,
            "alpha_mode": alpha_mode,
            "alpha_key": alpha_key if alpha_mode == "fourth_metric" else None
        }
        
        if b64_a is not None:
            result["b64_a"] = b64_a
            result["label_a_full_html"] = label_a_full_html
            result["label_a_short_html"] = label_a_short_html
        
        return result

    async def get_dynamic_viz_data(
        self, 
        workout_id: int, 
        r_key: str, 
        g_key: str, 
        b_key: str,
        alpha_mode: str = "none",
        alpha_key: str = "cadence"
    ) -> dict:
        """Public method to be called by the /viz API endpoint."""
        self._check_ready()
        doc_id = f"workout_rad_{workout_id}"
        doc = await self.db.workouts.find_one({"_id": doc_id})
        if not doc:
            raise RuntimeError(f"Doc {doc_id} not found")
        
        return await self._generate_viz_data(doc, r_key, g_key, b_key, alpha_mode, alpha_key)

    async def render_gallery_page(self, request_context: dict) -> str:
        """Renders the main gallery page."""
        self._check_ready()
        try:
            docs = await self.db.workouts.find(
                {}, 
                {"_id": 1, "time_series": 1}
            ).sort("_id", 1).limit(200).to_list(length=None)
        except Exception as e:
            logger.error(f"[{self.write_scope}-Actor] DB error in render_gallery_page: {e}")
            docs = []

        if not docs:
            snippet_list = ["<p>No workouts present. Click 'Generate' to create some!</p>"]
        else:
            semaphore = asyncio.Semaphore(10)
            
            def _generate_snippet_sync(doc: Dict[str, Any]) -> str:
                arrays = self._generate_workout_viz_arrays(
                    doc, size=8, r_key="heart_rate", g_key="calories_per_min", b_key="speed_kph"
                )
                b64_img = self._encode_png_b64(arrays["rgb_combined"], (128, 128))
                suffix = doc["_id"].split("_")[-1]
                return f"""
                  <div class="collection-item">
                    <a href="./workout/{suffix}">
                      <img src="data:image/png;base64,{b64_img}" alt="Workout {suffix}">
                      <p>Workout #{suffix}</p>
                    </a>
                  </div>
                """
            
            async def _generate_snippet_with_limit(doc: Dict[str, Any]) -> str:
                async with semaphore:
                    return await asyncio.to_thread(_generate_snippet_sync, doc)
            
            snippet_tasks = [_generate_snippet_with_limit(d) for d in docs]
            snippet_list = await asyncio.gather(*snippet_tasks)

        response = self.templates.TemplateResponse(
            "index.html",
            {"request": request_context, "collection_images_html": "".join(snippet_list)},
        )
        return response.body.decode("utf-8")

    async def render_detail_page(
        self, 
        workout_id: int, 
        request_context: dict, 
        r_key: str, 
        g_key: str, 
        b_key: str,
        alpha_mode: str = "none",
        alpha_key: str = "cadence",
        use_voyage: bool = True
    ) -> str:
        """Renders the workout detail page."""
        self._check_ready()
        
        doc_id = f"workout_rad_{workout_id}"
        doc = await self.db.workouts.find_one({"_id": doc_id})
        if not doc:
            return f"<h1>404 - Not Found</h1><p>No workout with id {doc_id}</p>"

        viz_data = await self._generate_viz_data(doc, r_key, g_key, b_key, alpha_mode, alpha_key)
        raw_data_for_charts = viz_data["raw_data"]

        summary_is_pending = (
            PLACEHOLDER_CLASSIFICATION in doc.get("ai_classification", "") or
            PLACEHOLDER_SUMMARY in doc.get("ai_summary", "")
        )

        neighbors_html = "<p>Vector data is missing, so no neighbors found.</p>"
        neighbors = []
        neighbors_data = []
        nearest_neighbors = []
        # MODIFIED: Check for 64-dim vector instead of 192
        if isinstance(doc.get("workout_vector"), list) and len(doc["workout_vector"]) == LATENT_DIM:
            current_vector = doc["workout_vector"]
            
            SEARCH_LIMIT = 25
            FINAL_TOP_K = 3
            VECTOR_MAGIC_TOP_K = 5
            
            pipeline = [
                {
                    "$vectorSearch": {
                        "index": self.vector_index_name,
                        "path": "workout_vector",
                        "queryVector": current_vector,
                        "numCandidates": 100,
                        "limit": SEARCH_LIMIT,
                        "filter": {"_id": {"$ne": doc_id}}
                    }
                },
                {
                    "$project": {
                        "_id": 1,
                        "score": {"$meta": "vectorSearchScore"},
                        "workout_type": 1,
                        "session_tag": 1,
                        "ai_classification": 1,
                        "rpe": 1,
                        "post_session_notes": 1
                    }
                }
            ]
            
            broad_neighbors_list = []
            try:
                neighbors_cursor = self.db.raw.workouts.aggregate(pipeline)
                broad_neighbors_list = await neighbors_cursor.to_list(None)
            except self.OperationFailure as oe:
                err_msg = oe.details.get('errmsg', str(oe))
                logger.error(f"[{self.write_scope}-Actor] VectorSearch error: {err_msg}")
                neighbors_html = f"<p><b>VectorSearch DB Error:</b> {err_msg}<br><small>Is index '{self.vector_index_name}' active?</small></p>"
                broad_neighbors_list = []
            except Exception as e:
                logger.error(f"[{self.write_scope}-Actor] Unexpected vector search error: {e}")
                neighbors_html = f"<p><b>Unexpected vector search error:</b> {e}</p>"
                broad_neighbors_list = []
            
            # Rerank with VoyageAI
            if broad_neighbors_list and self.voyage_client and use_voyage:
                logger.info(f"[{self.write_scope}-Actor] Vector search returned {len(broad_neighbors_list)} candidates. Reranking with VoyageAI...")
                
                rerank_query = self._get_doc_as_rerank_string(doc)
                rerank_docs = [self._get_doc_as_rerank_string(neighbor) for neighbor in broad_neighbors_list]
                
                try:
                    rerank_results = await self.voyage_client.rerank(
                        query=rerank_query,
                        documents=rerank_docs,
                        model=self.VOYAGE_RERANK_MODEL,
                        top_k=VECTOR_MAGIC_TOP_K
                    )
                    
                    for result in rerank_results.results:
                        original_neighbor = broad_neighbors_list[result.index]
                        original_neighbor['score'] = result.relevance_score
                        nearest_neighbors.append(original_neighbor)
                        
                    logger.info(f"[{self.write_scope}-Actor] VoyageAI reranking complete. True Top {len(nearest_neighbors)} found.")
                except Exception as e:
                    logger.error(f"[{self.write_scope}-Actor] VoyageAI rerank call failed: {e}. Proceeding with original vector search results.")
                    nearest_neighbors = broad_neighbors_list[:VECTOR_MAGIC_TOP_K]
            else:
                if not use_voyage:
                    logger.info(f"[{self.write_scope}-Actor] VoyageAI reranking disabled via use_voyage parameter. Using original vector search results.")
                elif not self.voyage_client:
                    logger.warning(f"[{self.write_scope}-Actor] VoyageAI client not available. Using original vector search results.")
                nearest_neighbors = broad_neighbors_list[:VECTOR_MAGIC_TOP_K]
            
            # Process Results for Display
            if nearest_neighbors:
                items = []
                for n in nearest_neighbors:
                    sid = n["_id"].split("_")[-1]
                    context_span = f"Type: {n.get('workout_type','?')}"
                    if n.get("session_tag"): context_span += f" | Tag: {n['session_tag']}"
                    if n.get("ai_classification") != PLACEHOLDER_CLASSIFICATION:
                        context_span += f" | Pattern: {n['ai_classification']}"
                    
                    items.append(f'<li><a href="/experiments/{self.write_scope}/workout/{sid}">Workout #{sid}</a> <span>({context_span})</span><br>Similarity Score: {n["score"]:.4f}</li>')
                    
                    neighbors_data.append({
                        "workout_id": int(sid),
                        "workout_type": n.get("workout_type", "?"),
                        "session_tag": n.get("session_tag"),
                        "ai_classification": n.get("ai_classification"),
                        "score": float(n.get("score", 0.0)),
                        "rpe": n.get("rpe")
                    })

                neighbors_html = "".join(items[:FINAL_TOP_K])
            else:
                neighbors_html = "<p>No neighbors found (maybe only 1 doc in DB?).</p>"
        
        ephemeral_prompt = doc.get("llm_analysis_prompt", PLACEHOLDER_PROMPT)
        ai_class = doc.get("ai_classification", PLACEHOLDER_CLASSIFICATION)
        ai_sum = doc.get("ai_summary", PLACEHOLDER_SUMMARY)

        if summary_is_pending:
            ephemeral_class, ephemeral_prompt = self._analyze_time_series_features(doc, nearest_neighbors[:3] if nearest_neighbors else neighbors)
            ai_class = ephemeral_class
        else:
            ephemeral_prompt = doc.get("llm_analysis_prompt", PLACEHOLDER_PROMPT)

        all_charts = {
            "heart_rate": self._generate_chart_base64(raw_data_for_charts.get("heart_rate", []), "#FF6868"),
            "calories_per_min": self._generate_chart_base64(raw_data_for_charts.get("calories_per_min", []), "#00ED64"),
            "speed_kph": self._generate_chart_base64(raw_data_for_charts.get("speed_kph", []), "#58AEFF"),
            "power": self._generate_chart_base64(raw_data_for_charts.get("power", []), "#FFA554"),
            "cadence": self._generate_chart_base64(raw_data_for_charts.get("cadence", []), "#C792EA")
        }

        doc_copy = dict(doc)
        if isinstance(doc_copy.get("workout_vector"), list):
            vec_len = len(doc_copy["workout_vector"])
            short_vec = doc_copy["workout_vector"][:5]
            doc_copy["workout_vector"] = f"[{short_vec[0]:.2f}... {vec_len - 1} more elements]"
        doc_json = json.dumps(doc_copy, indent=2, default=str)

        gear_used_html = ""
        if doc.get("gear_used"):
             gear_used_html = "<ul>"
             for g in doc.get("gear_used", []):
                 item_name = g.get("item", "Unknown")
                 details = []
                 for key, value in g.items():
                     if key != "item":
                         if key == "kilometers":
                             details.append(f"{value} km")
                         elif key == "battery_life_percent":
                             details.append(f"{value}% battery")
                         else:
                             key_formatted = key.replace('_', ' ').title()
                             details.append(f"{key_formatted}: {value}")
                 if details:
                     gear_used_html += f"<li><strong>{item_name}</strong>: {', '.join(details)}</li>"
                 else:
                     gear_used_html += f"<li><strong>{item_name}</strong></li>"
             gear_used_html += "</ul>"
        
        if summary_is_pending:
            ai_analysis_button_html = f"""
              <form id="analyzeForm" action="/experiments/{self.write_scope}/workout/{workout_id}/analyze" method="POST" style="margin:0;">
                <button type="submit" id="analyzeBtn" class="control-btn" style="background-color:var(--accent-blue);color:white;">
                  <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"
                       fill="currentColor" viewBox="0 0 16 16"
                       style="vertical-align:-2px;margin-right:5px;">
                    <path d="M8 15A7 7 0 1 0 8 1a7 7 0 0 0 0 14zm-5.467 4.14C7.02 12.637 7.558 13 8 13c.448 0 .89-.37 1.341-.758.384-.33 1.164-.98 1.956-1.579.529-.396.958-.87 1.253-1.412.308-.567.452-1.217.452-1.921 0-.663-.122-1.284-.367-1.841-.247-.568-.62-1.11-1.12-1.583-.497-.47-1.127-.866-1.87-1.171C9.697 5.093 8.87 4.75 8 4.75c-.878 0-1.688.354-2.457.784-.735.41-1.353.94-1.854 1.572-.497.625-.873 1.342-1.124 2.144-.25.808-.372 1.68-.372 2.616 0 .666.126 1.298.375 1.879.248.568.618 1.107 1.117 1.582.497.47 1.127.865 1.87 1.171z"/>
                    <path fill-rule="evenodd" d="M8 15A7 7 0 1 0 8 1a7 7 0 0 0 0 14zM8 2A6 6 0 1 1 8 14 6 6 0 0 1 8 2z"/>
                  </svg>
                  Generate AI Summary
                </button>
              </form>
            """
        else:
            ai_analysis_button_html = '<span style="color: var(--atlas-green); font-weight:600;">Analysis Complete</span>'

        context = {
            "request": request_context,
            "workout_id": workout_id,
            "all_charts": all_charts,
            "json_data_pretty": doc_json,
            "ai_neighbors_html": neighbors_html,
            "neighbors_data": neighbors_data,
            "current_workout_rpe": doc.get("rpe"),
            "current_workout_session_tag": doc.get("session_tag"),
            "ai_classification": ai_class,
            "ai_summary": ai_sum,
            "llm_analysis_prompt": ephemeral_prompt,
            "ai_analysis_button_html": ai_analysis_button_html,
            "b64_combined": viz_data["b64_combined"],
            "b64_r": viz_data["b64_r"],
            "b64_g": viz_data["b64_g"],
            "b64_b": viz_data["b64_b"],
            "b64_a": viz_data.get("b64_a"),
            "label_r_full_html": viz_data["label_r_full_html"],
            "label_g_full_html": viz_data["label_g_full_html"],
            "label_b_full_html": viz_data["label_b_full_html"],
            "label_a_full_html": viz_data.get("label_a_full_html"),
            "label_r_short_html": viz_data["label_r_short_html"],
            "label_g_short_html": viz_data["label_g_short_html"],
            "label_b_short_html": viz_data["label_b_short_html"],
            "label_a_short_html": viz_data.get("label_a_short_html"),
            "all_metrics": AVAILABLE_METRICS,
            "selected_r_key": r_key,
            "selected_g_key": g_key,
            "selected_b_key": b_key,
            "alpha_mode": alpha_mode,
            "alpha_key": alpha_key if alpha_mode == "fourth_metric" else None,
            "workout_type": doc.get("workout_type", "N/A"),
            "session_tag": doc.get("session_tag", "N/A"),
            "gear_used_html": gear_used_html,
            "sets_reps_html": "", 
            "cycling_html": "", 
            "yoga_html": "",
            "post_session_notes_html": self._format_post_session_notes(doc.get('post_session_notes', {})),
            "vector_index_name": self.vector_index_name,
        }
        
        response = self.templates.TemplateResponse("detail.html", context)
        return response.body.decode("utf-8")

    async def run_analysis(self, workout_id: int, use_voyage: bool = True) -> bool:
        """Runs AI analysis on a workout."""
        self._check_ready()
        
        doc_id = f"workout_rad_{workout_id}"
        doc = await self.db.workouts.find_one({"_id": doc_id})
        if not doc:
            logger.error(f"[{self.write_scope}-Actor] run_analysis: Doc {doc_id} not found.")
            return False

        OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
        if not OPENAI_API_KEY:
            logger.error(f"[{self.write_scope}-Actor] OPENAI_API_KEY not set. Cannot perform AI workout analysis.")
            return False
            
        if use_voyage and not self.voyage_client:
            logger.error(f"[{self.write_scope}-Actor] VOYAGE_API_KEY not set or client failed to init. Reranking is disabled.")
            return False

        # MODIFIED: Check for 64-dim vector instead of 192
        if "workout_vector" not in doc or not isinstance(doc["workout_vector"], list) or len(doc["workout_vector"]) != LATENT_DIM:
            logger.error(f"[{self.write_scope}-Actor] Doc {doc_id} missing or invalid vector data (expected {LATENT_DIM} dims).")
            return False

        current_vector = doc["workout_vector"]
        
        SEARCH_LIMIT = 25
        FINAL_TOP_K = 3
        
        pipeline = [
            {
                "$vectorSearch": {
                    "index": self.vector_index_name,
                    "path": "workout_vector",
                    "queryVector": current_vector,
                    "numCandidates": 100,
                    "limit": SEARCH_LIMIT,
                    "filter": {"_id": {"$ne": doc_id}}
                }
            },
            {
                "$project": {
                    "_id": 1,
                    "score": {"$meta": "vectorSearchScore"},
                    "workout_type": 1,
                    "session_tag": 1,
                    "ai_classification": 1,
                    "post_session_notes": 1
                }
            }
        ]
        
        broad_neighbors_list = []
        try:
            neighbors_cursor = self.db.raw.workouts.aggregate(pipeline)
            broad_neighbors_list = await neighbors_cursor.to_list(None)
        except self.OperationFailure:
            logger.warning(f"[{self.write_scope}-Actor] Vector search failed during analysis, proceeding without neighbors context.")
            broad_neighbors_list = []
        
        nearest_neighbors = []
        
        if broad_neighbors_list and self.voyage_client and use_voyage:
            logger.info(f"[{self.write_scope}-Actor] Vector search returned {len(broad_neighbors_list)} candidates. Reranking with VoyageAI...")
            
            rerank_query = self._get_doc_as_rerank_string(doc)
            rerank_docs = [self._get_doc_as_rerank_string(neighbor) for neighbor in broad_neighbors_list]
            
            try:
                rerank_results = await self.voyage_client.rerank(
                    query=rerank_query,
                    documents=rerank_docs,
                    model=self.VOYAGE_RERANK_MODEL,
                    top_k=FINAL_TOP_K
                )
                
                for result in rerank_results.results:
                    original_neighbor = broad_neighbors_list[result.index]
                    original_neighbor['score'] = result.relevance_score
                    nearest_neighbors.append(original_neighbor)
                    
                logger.info(f"[{self.write_scope}-Actor] VoyageAI reranking complete. True Top {len(nearest_neighbors)} found.")
            except Exception as e:
                logger.error(f"[{self.write_scope}-Actor] VoyageAI rerank call failed: {e}. Proceeding with original vector search results.")
                nearest_neighbors = broad_neighbors_list[:FINAL_TOP_K]
        else:
            if not use_voyage:
                logger.info(f"[{self.write_scope}-Actor] VoyageAI reranking disabled via use_voyage parameter. Using original vector search results.")
            nearest_neighbors = broad_neighbors_list[:FINAL_TOP_K]

        final_class, final_prompt = self._analyze_time_series_features(doc, nearest_neighbors)
        
        summary = await self._call_openai_api(final_prompt)
        
        await self.db.workouts.update_one(
            {"_id": doc_id},
            {"$set": {"ai_classification": final_class, "ai_summary": summary, "llm_analysis_prompt": final_prompt}}
        )
        logger.info(f"[{self.write_scope}-Actor] Analysis complete for {doc_id}.")
        return True

    def _format_post_session_notes(self, notes: dict) -> str:
        """Formats post-session notes as readable HTML instead of raw JSON."""
        if not notes:
            return "<em>No notes</em>"
        
        formatted_parts = []
        for key, value in notes.items():
            key_formatted = key.replace('_', ' ').title()
            if key == "hydration_ml":
                formatted_parts.append(f"<strong>Hydration:</strong> {value} ml")
            elif key == "notes":
                formatted_parts.append(f"<strong>Notes:</strong> {value}")
            else:
                formatted_parts.append(f"<strong>{key_formatted}:</strong> {value}")
        
        return ", ".join(formatted_parts)

    def get_model_status(self) -> dict:
        """
        Returns the status of the PyTorch model loading.
        Useful for debugging.
        """
        import os
        import pathlib
        
        # Check what paths exist
        hardcoded_path = "/app/experiments/data_imaging_advanced/workout_encoder.pth"
        exp_dir_path = str(experiment_dir / DEFAULT_MODEL_PATH)
        
        # Try to manually load the model if it's not loaded
        error_msg = None
        if self.encoder_model is None and self.torch is not None:
            try:
                model_path = hardcoded_path
                if not os.path.exists(model_path):
                    model_path = exp_dir_path
                
                if os.path.exists(model_path):
                    device = self.torch.device("cuda" if self.torch.cuda.is_available() else "cpu")
                    autoencoder_model = self._create_autoencoder_model()
                    encoder_to_load = autoencoder_model.encoder
                    state_dict = self.torch.load(model_path, map_location=device)
                    encoder_to_load.load_state_dict(state_dict)
                    encoder_to_load.to(device)
                    encoder_to_load.eval()
                    self.encoder_model = encoder_to_load
                    self.device = device
                    self.model_path_used = model_path
                    error_msg = "Model loaded successfully via manual load"
                else:
                    error_msg = f"Model file not found at {model_path}"
            except Exception as e:
                error_msg = f"Manual load failed: {str(e)}"
                import traceback
                error_msg += f"\n{traceback.format_exc()}"
        
        return {
            "model_loaded": self.encoder_model is not None,
            "torch_available": self.torch is not None,
            "model_path": self.model_path_used if hasattr(self, 'model_path_used') else None,
            "device": str(self.device) if hasattr(self, 'device') else None,
            "encoder_model_type": str(type(self.encoder_model)) if self.encoder_model else None,
            "error": error_msg,
            "debug": {
                "experiment_dir": str(experiment_dir),
                "experiment_dir_resolved": str(experiment_dir.resolve()) if hasattr(experiment_dir, 'resolve') else None,
                "hardcoded_path": hardcoded_path,
                "hardcoded_path_exists": os.path.exists(hardcoded_path),
                "exp_dir_path": exp_dir_path,
                "exp_dir_path_exists": os.path.exists(exp_dir_path),
                "cwd": str(pathlib.Path.cwd()),
            }
        }
    
    async def compare_encodings(self, workout_id: int) -> dict:
        """
        Compares the hardcoded 192-dim encoding vs the learned 64-dim encoding.
        Returns both vectors, their statistics, and similarity metrics.
        """
        self._check_ready()
        
        doc_id = f"workout_rad_{workout_id}"
        doc = await self.db.workouts.find_one({"_id": doc_id})
        if not doc:
            raise RuntimeError(f"Doc {doc_id} not found")
        
        # Generate both encodings
        learned_vector = self._get_feature_vector(doc)  # 64-dim PyTorch encoder
        hardcoded_vector = self._get_hardcoded_feature_vector(doc)  # 192-dim flattening
        
        # Calculate statistics
        learned_stats = {
            "dim": len(learned_vector),
            "mean": float(self.np.mean(learned_vector)),
            "std": float(self.np.std(learned_vector)),
            "min": float(self.np.min(learned_vector)),
            "max": float(self.np.max(learned_vector)),
            "norm": float(self.np.linalg.norm(learned_vector))
        }
        
        hardcoded_stats = {
            "dim": len(hardcoded_vector),
            "mean": float(self.np.mean(hardcoded_vector)),
            "std": float(self.np.std(hardcoded_vector)),
            "min": float(self.np.min(hardcoded_vector)),
            "max": float(self.np.max(hardcoded_vector)),
            "norm": float(self.np.linalg.norm(hardcoded_vector))
        }
        
        # Find neighbors using each method
        learned_neighbors = []
        hardcoded_neighbors = []
        
        # Get neighbors using learned encoding (if stored in doc)
        if isinstance(doc.get("workout_vector"), list) and len(doc["workout_vector"]) == LATENT_DIM:
            try:
                pipeline = [
                    {
                        "$vectorSearch": {
                            "index": self.vector_index_name,
                            "path": "workout_vector",
                            "queryVector": doc["workout_vector"],
                            "numCandidates": 50,
                            "limit": 5,
                            "filter": {"_id": {"$ne": doc_id}}
                        }
                    },
                    {
                        "$project": {
                            "_id": 1,
                            "score": {"$meta": "vectorSearchScore"},
                            "workout_type": 1,
                            "session_tag": 1
                        }
                    }
                ]
                cursor = self.db.raw.workouts.aggregate(pipeline)
                learned_neighbors = await cursor.to_list(length=None)
            except Exception as e:
                logger.warning(f"Failed to get learned neighbors: {e}")
        
        # Get neighbors using hardcoded encoding (compute on-the-fly)
        try:
            # Fetch all other workouts
            all_docs = await self.db.workouts.find(
                {"_id": {"$ne": doc_id}},
                {"_id": 1, "time_series": 1, "workout_type": 1, "session_tag": 1}
            ).limit(100).to_list(length=None)
            
            # Compute cosine similarity with hardcoded vectors
            similarities = []
            hardcoded_norm = self.np.linalg.norm(hardcoded_vector)
            
            for other_doc in all_docs:
                try:
                    other_hardcoded = self._get_hardcoded_feature_vector(other_doc)
                    other_norm = self.np.linalg.norm(other_hardcoded)
                    
                    if hardcoded_norm > 0 and other_norm > 0:
                        similarity = float(self.np.dot(hardcoded_vector, other_hardcoded) / (hardcoded_norm * other_norm))
                        suffix = other_doc["_id"].split("_")[-1]
                        similarities.append({
                            "_id": other_doc["_id"],
                            "workout_id": int(suffix),
                            "score": similarity,
                            "workout_type": other_doc.get("workout_type", "?"),
                            "session_tag": other_doc.get("session_tag")
                        })
                except Exception as e:
                    logger.warning(f"Error computing similarity for {other_doc.get('_id')}: {e}")
                    continue
            
            # Sort by similarity and take top 5
            similarities.sort(key=lambda x: x["score"], reverse=True)
            hardcoded_neighbors = similarities[:5]
        except Exception as e:
            logger.warning(f"Failed to get hardcoded neighbors: {e}")
        
        return {
            "workout_id": workout_id,
            "learned": {
                "vector": learned_vector.tolist(),
                "stats": learned_stats,
                "neighbors": [
                    {
                        "workout_id": int(n["_id"].split("_")[-1]),
                        "score": float(n.get("score", 0)),
                        "workout_type": n.get("workout_type", "?"),
                        "session_tag": n.get("session_tag")
                    }
                    for n in learned_neighbors
                ]
            },
            "hardcoded": {
                "vector": hardcoded_vector.tolist(),
                "stats": hardcoded_stats,
                "neighbors": hardcoded_neighbors
            }
        }

