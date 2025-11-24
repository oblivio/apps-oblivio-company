#!/usr/bin/env python3
"""
Demo script for data_imaging_advanced experiment.

This script demonstrates:
1. Synthetic workout data generation
2. PyTorch encoder embedding generation (64-dim)
3. Hardcoded embedding generation (192-dim) for comparison
4. RGB visualization of workout data
5. Statistical analysis of embeddings

Usage:
    python demo.py [--model-path MODEL_PATH] [--num-workouts N] [--save-images]
"""

import argparse
import pathlib
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image
import io
import base64
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import json

# Configuration matching actor.py
LATENT_DIM = 64
PLACEHOLDER_CLASSIFICATION = "Pending Analysis"
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

# Experiment directory
experiment_dir = pathlib.Path(__file__).parent
DEFAULT_MODEL_PATH = experiment_dir / "workout_encoder.pth"


def create_autoencoder_model():
    """Creates the autoencoder model architecture matching the training script."""
    try:
        import torch
        import torch.nn as nn
    except ImportError:
        print("❌ PyTorch not installed. Install with: pip install torch")
        return None
    
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


def load_encoder_model(model_path: Optional[str] = None):
    """Loads the PyTorch encoder model."""
    try:
        import torch
    except ImportError:
        print("❌ PyTorch not installed. Install with: pip install torch")
        return None, None
    
    if model_path is None:
        model_path = DEFAULT_MODEL_PATH
    
    model_path = pathlib.Path(model_path)
    
    if not model_path.exists():
        print(f"⚠️  Model file not found at: {model_path}")
        print(f"   Generate it with: python train_model.py --output-path {model_path}")
        return None, None
    
    print(f"📦 Loading encoder model from: {model_path}")
    
    try:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"   Using device: {device}")
        
        autoencoder_model = create_autoencoder_model()
        if autoencoder_model is None:
            return None, None
        
        encoder = autoencoder_model.encoder
        state_dict = torch.load(model_path, map_location=device)
        encoder.load_state_dict(state_dict)
        encoder.to(device)
        encoder.eval()
        
        print(f"✅ Model loaded successfully")
        return encoder, device
    except Exception as e:
        print(f"❌ Failed to load model: {e}")
        import traceback
        traceback.print_exc()
        return None, None


def normalize_for_model(data, min_val: float, max_val: float):
    """Clips and normalizes a NumPy array to a 0.0 - 1.0 float scale."""
    clipped_data = np.clip(data, min_val, max_val)
    range_val = max_val - min_val
    if range_val == 0:
        return np.zeros_like(clipped_data, dtype=np.float32)
    normalized = (clipped_data - min_val) / range_val
    return normalized.astype(np.float32)


def preprocess_for_model(doc: dict):
    """Takes a workout doc, validates, normalizes [0,1], and stacks it into (3, 64) format."""
    try:
        ts = doc['time_series']
        hr_data = np.array(ts['heart_rate'])
        cal_data = np.array(ts['calories_per_min'])
        speed_data = np.array(ts['speed_kph'])
        
        if not (len(hr_data) == 64 and len(cal_data) == 64 and len(speed_data) == 64):
            return None
        
        hr_norm = normalize_for_model(hr_data, *NORM_BOUNDS["heart_rate"])
        cal_norm = normalize_for_model(cal_data, *NORM_BOUNDS["calories_per_min"])
        speed_norm = normalize_for_model(speed_data, *NORM_BOUNDS["speed_kph"])
        
        # PyTorch Conv1d expects (Batch, Channels, Length)
        model_input = np.stack([hr_norm, cal_norm, speed_norm], axis=0)
        return model_input.astype(np.float32)
    
    except Exception as e:
        print(f"Error in preprocess_for_model: {e}")
        return None


def get_feature_vector_pytorch(doc: dict, encoder_model, device):
    """Generates 64-dim embedding using PyTorch encoder."""
    if encoder_model is None:
        return np.zeros(LATENT_DIM, dtype=np.float32)
    
    try:
        import torch
        
        model_input_np = preprocess_for_model(doc)
        if model_input_np is None:
            return np.zeros(LATENT_DIM, dtype=np.float32)
        
        model_input_tensor = torch.from_numpy(model_input_np).unsqueeze(0).to(device)
        
        with torch.no_grad():
            latent_vector_tensor = encoder_model(model_input_tensor)
        
        latent_vector_np = latent_vector_tensor.squeeze(0).cpu().numpy()
        return latent_vector_np
        
    except Exception as e:
        print(f"Error in get_feature_vector_pytorch: {e}")
        return np.zeros(LATENT_DIM, dtype=np.float32)


def get_hardcoded_feature_vector(doc: dict):
    """Generates the hardcoded 192-dim vector (8x8x3 flattening) for comparison."""
    arrays = generate_workout_viz_arrays(
        doc, 
        size=8,
        r_key="heart_rate",
        g_key="calories_per_min",
        b_key="speed_kph",
        alpha_mode="none"
    )
    return arrays["rgb_combined"].reshape(-1).astype(np.float32)


def create_synthetic_apple_watch_data(suffix: int) -> dict:
    """Generates synthetic data with random variations."""
    np.random.seed(suffix)
    t = np.linspace(0, 2 * np.pi, 64)

    hr_base = 100 + (suffix % 7) * 5
    cal_base = 5 + (suffix % 5) * 1
    speed_base = 3.5 + (suffix % 6) * 0.5

    hr_array = hr_base + 60 * np.sin(t + np.random.rand() * 0.5) + np.random.rand(64) * 10
    hr_array[:5] *= 0.8
    hr_array[-5:] *= 0.9

    cal_array = cal_base + 4 * np.sin(t + np.random.rand() * 0.3) + np.random.rand(64) * 2

    spd_array = np.full(64, speed_base) + np.random.rand(64) * 0.4
    spd_array[:5] = 2.0 + np.random.rand(5) * 0.4
    spd_array[-5:] = 1.2 + np.random.rand(5) * 0.3

    power_base = 150 + (suffix % 8) * 10
    power_array = power_base + 50 * np.sin(t + np.random.rand() * 0.7) + np.random.rand(64) * 15
    power_array[power_array < 0] = 0

    cadence_base = 80 + (suffix % 4) * 5
    cadence_array = np.full(64, cadence_base) + np.random.rand(64) * 3
    cadence_array[10:15] = 0 
    cadence_array[40:45] = 0

    data_quality = np.full(64, 255, dtype=np.uint8)
    dropout_indices = np.random.choice(64, size=min(5, 64), replace=False)
    data_quality[dropout_indices] = 0
    data_quality[10:15] = 0
    data_quality[40:45] = 0

    intensity_pattern = suffix % 10
    if intensity_pattern in [0, 1, 2, 7, 8]:
        session_tag = str(np.random.choice(["Tempo Pace", "Threshold", "Race Day", "High Intensity Interval"]))
        rpe = float(np.random.randint(7, 10))
    elif intensity_pattern in [5, 6]:
        session_tag = str(np.random.choice(["Recovery", "Z2 Cardio", "Easy Recovery Run"]))
        rpe = float(np.random.randint(2, 5))
    else:
        session_tag = str(np.random.choice(["Race Day", "Recovery", "Z2 Cardio", "Tempo Pace", "Threshold"]))
        rpe = float(np.random.randint(4, 8))

    doc_id = f"workout_rad_{suffix}"
    return {
        "_id": doc_id,
        "time_series": {
            "heart_rate": np.round(np.maximum(hr_array, NORM_BOUNDS["heart_rate"][0]), 2).tolist(),
            "calories_per_min": np.round(np.maximum(cal_array, NORM_BOUNDS["calories_per_min"][0]), 2).tolist(),
            "speed_kph": np.round(np.maximum(spd_array, NORM_BOUNDS["speed_kph"][0]), 2).tolist(),
            "power": np.round(np.maximum(power_array, NORM_BOUNDS["power"][0]), 2).tolist(),
            "cadence": np.round(np.maximum(cadence_array, NORM_BOUNDS["cadence"][0]), 2).tolist(),
        },
        "data_quality": data_quality.tolist(),
        "rpe": rpe,
        "start_time": datetime(2025, 10, 27, 10, 10 + (suffix % 40), 0, tzinfo=timezone.utc),
        "workout_type": str(np.random.choice(["Outdoor Run", "Cycling", "Strength", "Yoga"])),
        "session_tag": session_tag,
        "post_session_notes": {
            "hydration_ml": int(np.random.randint(500, 2500)),
            "notes": str(np.random.choice(["Felt good", "Legs sore", "Pushed harder", "Casual run"])),
        },
        "gear_used": [
            {"item": "shoes_v3", "kilometers": float(np.random.randint(50, 200))},
            {"item": "hrm_strap", "battery_life_percent": int(np.random.randint(10, 100))},
        ],
        "ai_classification": PLACEHOLDER_CLASSIFICATION,
    }


def norm_array(x, lo, hi):
    """Clips and normalizes a NumPy array to 0-255 uint8."""
    x_clipped = np.clip(x, lo, hi)
    rng = hi - lo
    if rng <= 0:
        return np.zeros_like(x_clipped, dtype=np.uint8)
    return ((x_clipped - lo) / rng * 255).astype(np.uint8)


def generate_workout_viz_arrays(
    doc: dict, 
    size=8, 
    r_key: str = "heart_rate", 
    g_key: str = "calories_per_min", 
    b_key: str = "speed_kph",
    alpha_mode: str = "none",
    alpha_key: str = "cadence"
):
    """Generates the normalized 8x8x3 RGB or 8x8x4 RGBA array plus raw arrays."""
    
    def get_raw_data(key):
        return doc.get("time_series", {}).get(key, [0]*64)

    raw_data = {key: np.array(get_raw_data(key), dtype=float) for key in AVAILABLE_METRICS}
    
    r_bounds = NORM_BOUNDS.get(r_key, (0, 1))
    g_bounds = NORM_BOUNDS.get(g_key, (0, 1))
    b_bounds = NORM_BOUNDS.get(b_key, (0, 1))

    r_1d = norm_array(raw_data.get(r_key, np.zeros(64)), *r_bounds)
    g_1d = norm_array(raw_data.get(g_key, np.zeros(64)), *r_bounds)
    b_1d = norm_array(raw_data.get(b_key, np.zeros(64)), *b_bounds)

    r_2d = r_1d.reshape(size, size)
    g_2d = g_1d.reshape(size, size)
    b_2d = b_1d.reshape(size, size)

    alpha_2d = None
    if alpha_mode == "fourth_metric":
        alpha_bounds = NORM_BOUNDS.get(alpha_key, (0, 1))
        alpha_data = np.array(get_raw_data(alpha_key), dtype=float)
        alpha_1d = norm_array(alpha_data, *alpha_bounds)
        alpha_2d = alpha_1d.reshape(size, size)
    elif alpha_mode == "data_quality":
        data_quality = doc.get("data_quality", [255]*64)
        alpha_1d = np.array(data_quality, dtype=np.uint8)
        alpha_2d = alpha_1d.reshape(size, size)
    elif alpha_mode == "global_rpe":
        rpe = doc.get("rpe", 5.0)
        rpe_normalized = int(np.clip((rpe - 1) / 9 * 255, 0, 255))
        alpha_2d = np.full((size, size), rpe_normalized, dtype=np.uint8)

    if alpha_mode != "none" and alpha_2d is not None:
        combined = np.stack([r_2d, g_2d, b_2d, alpha_2d], axis=-1)
    else:
        combined = np.stack([r_2d, g_2d, b_2d], axis=-1)
    
    return {
        "rgb_combined": combined,
        "raw_data": raw_data, 
        "channel_r_2d": r_2d,
        "channel_g_2d": g_2d,
        "channel_b_2d": b_2d,
    }


def save_image(array, filename: str, size=(256, 256)):
    """Saves a NumPy array as a PNG image."""
    if array.ndim == 2:
        img = Image.fromarray(array, "L")
    elif array.shape[-1] == 4:
        img = Image.fromarray(array, "RGBA")
    elif array.shape[-1] == 3:
        img = Image.fromarray(array, "RGB")
    else:
        img = Image.fromarray(array, "RGB")
    
    if size:
        img = img.resize(size, Image.NEAREST)
    
    img.save(filename)
    print(f"   💾 Saved: {filename}")


def generate_chart(data, color="#FF6868", title="Time Series"):
    """Generates a Matplotlib line chart."""
    arr = np.array(data, dtype=float)
    
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(4.5, 2.5), dpi=100)
    fig.patch.set_facecolor("#132A38")
    ax.set_facecolor("#132A38")

    ax.plot(arr, color=color, linewidth=2)
    ax.set_xlim(0, len(arr)-1 if len(arr) > 1 else 1)
    ax.tick_params(axis="x", colors="#A7B6C2")
    ax.tick_params(axis="y", colors="#A7B6C2")
    ax.set_xticks([0, len(arr)-1])
    ax.set_xticklabels(["Start", "End"])
    ax.set_title(title, color="#A7B6C2")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color("#23435B")
    ax.spines["left"].set_color("#23435B")

    return fig


def print_vector_stats(vector, name: str):
    """Prints statistics about a vector."""
    print(f"\n📊 {name} Statistics:")
    print(f"   Dimensions: {len(vector)}")
    print(f"   Mean: {np.mean(vector):.6f}")
    print(f"   Std:  {np.std(vector):.6f}")
    print(f"   Min:  {np.min(vector):.6f}")
    print(f"   Max:  {np.max(vector):.6f}")
    print(f"   Norm: {np.linalg.norm(vector):.6f}")


def compare_encodings(learned_vec, hardcoded_vec):
    """Compares learned and hardcoded encodings."""
    print("\n" + "="*60)
    print("🔬 ENCODING COMPARISON")
    print("="*60)
    
    print_vector_stats(learned_vec, "Learned (PyTorch, 64-dim)")
    print_vector_stats(hardcoded_vec, "Hardcoded (8x8x3, 192-dim)")
    
    # Note: Can't directly compare dimensions, but can show they're different approaches
    print("\n💡 Note: These are different embedding approaches:")
    print("   - Learned: 64-dim latent space from PyTorch autoencoder")
    print("   - Hardcoded: 192-dim flattened RGB image (8x8x3)")


def demo_workout(workout_id: int, encoder_model, device, save_images: bool = False):
    """Demonstrates a single workout."""
    print(f"\n{'='*60}")
    print(f"🏃 WORKOUT #{workout_id} DEMO")
    print(f"{'='*60}")
    
    # Generate workout data
    doc = create_synthetic_apple_watch_data(workout_id)
    print(f"\n✅ Generated workout data:")
    print(f"   ID: {doc['_id']}")
    print(f"   Type: {doc['workout_type']}")
    print(f"   Session Tag: {doc['session_tag']}")
    print(f"   RPE: {doc['rpe']}/10")
    
    # Generate embeddings
    print(f"\n🧠 Generating embeddings...")
    learned_vec = get_feature_vector_pytorch(doc, encoder_model, device)
    hardcoded_vec = get_hardcoded_feature_vector(doc)
    
    if encoder_model is None:
        print("   ⚠️  PyTorch encoder not available, using zero vector")
    else:
        print(f"   ✅ Learned embedding: {len(learned_vec)}-dim")
    print(f"   ✅ Hardcoded embedding: {len(hardcoded_vec)}-dim")
    
    # Compare encodings
    compare_encodings(learned_vec, hardcoded_vec)
    
    # Generate visualizations
    print(f"\n🎨 Generating visualizations...")
    arrays = generate_workout_viz_arrays(
        doc,
        size=8,
        r_key="heart_rate",
        g_key="calories_per_min",
        b_key="speed_kph"
    )
    
    if save_images:
        output_dir = experiment_dir / "demo_output"
        output_dir.mkdir(exist_ok=True)
        
        # Save RGB combined image
        save_image(arrays["rgb_combined"], output_dir / f"workout_{workout_id}_rgb.png", size=(256, 256))
        
        # Save individual channels
        save_image(arrays["channel_r_2d"], output_dir / f"workout_{workout_id}_channel_r.png", size=(128, 128))
        save_image(arrays["channel_g_2d"], output_dir / f"workout_{workout_id}_channel_g.png", size=(128, 128))
        save_image(arrays["channel_b_2d"], output_dir / f"workout_{workout_id}_channel_b.png", size=(128, 128))
        
        # Save time series charts
        for metric, color in [
            ("heart_rate", "#FF6868"),
            ("calories_per_min", "#00ED64"),
            ("speed_kph", "#58AEFF"),
            ("power", "#FFA554"),
            ("cadence", "#C792EA")
        ]:
            if metric in arrays["raw_data"]:
                fig = generate_chart(arrays["raw_data"][metric], color, metric.replace("_", " ").title())
                fig.savefig(output_dir / f"workout_{workout_id}_{metric}.png", 
                           facecolor=fig.get_facecolor(), bbox_inches='tight', pad_inches=0.1)
                plt.close(fig)
                print(f"   💾 Saved: {output_dir / f'workout_{workout_id}_{metric}.png'}")
    
    # Print time series summary
    print(f"\n📈 Time Series Summary:")
    for metric in AVAILABLE_METRICS:
        if metric in arrays["raw_data"]:
            data = arrays["raw_data"][metric]
            print(f"   {metric:20s}: mean={np.mean(data):6.2f}, std={np.std(data):6.2f}, "
                  f"min={np.min(data):6.2f}, max={np.max(data):6.2f}")
    
    return doc, learned_vec, hardcoded_vec


def main():
    parser = argparse.ArgumentParser(description="Demo script for data_imaging_advanced")
    parser.add_argument(
        "--model-path",
        type=str,
        default=None,
        help="Path to the PyTorch encoder model (default: experiments/data_imaging_advanced/workout_encoder.pth)"
    )
    parser.add_argument(
        "--num-workouts",
        type=int,
        default=3,
        help="Number of workouts to generate and demo (default: 3)"
    )
    parser.add_argument(
        "--save-images",
        action="store_true",
        help="Save visualization images to demo_output/ directory"
    )
    parser.add_argument(
        "--workout-id",
        type=int,
        default=None,
        help="Specific workout ID to demo (overrides --num-workouts)"
    )
    
    args = parser.parse_args()
    
    print("="*60)
    print("🚀 DATA IMAGING ADVANCED - DEMO")
    print("="*60)
    
    # Load encoder model
    encoder_model, device = load_encoder_model(args.model_path)
    
    if encoder_model is None:
        print("\n⚠️  Running without PyTorch encoder model.")
        print("   Embeddings will be zero vectors.")
        print("   To enable full functionality:")
        print(f"   1. Train model: python train_model.py --output-path {DEFAULT_MODEL_PATH}")
        print(f"   2. Run demo: python demo.py --model-path {DEFAULT_MODEL_PATH}")
    else:
        print(f"\n✅ PyTorch encoder loaded and ready")
    
    # Run demos
    workouts_data = []
    
    if args.workout_id is not None:
        # Demo specific workout
        doc, learned, hardcoded = demo_workout(args.workout_id, encoder_model, device, args.save_images)
        workouts_data.append({
            "workout_id": args.workout_id,
            "learned_embedding": learned.tolist(),
            "hardcoded_embedding": hardcoded.tolist()
        })
    else:
        # Demo multiple workouts
        for i in range(args.num_workouts):
            doc, learned, hardcoded = demo_workout(i, encoder_model, device, args.save_images)
            workouts_data.append({
                "workout_id": i,
                "learned_embedding": learned.tolist(),
                "hardcoded_embedding": hardcoded.tolist()
            })
    
    # Summary
    print(f"\n{'='*60}")
    print("📋 DEMO SUMMARY")
    print(f"{'='*60}")
    print(f"✅ Generated {len(workouts_data)} workout(s)")
    if args.save_images:
        print(f"✅ Images saved to: {experiment_dir / 'demo_output'}")
    
    # Save embeddings to JSON
    if args.save_images:
        output_dir = experiment_dir / "demo_output"
        output_dir.mkdir(exist_ok=True)
        json_path = output_dir / "embeddings.json"
        with open(json_path, 'w') as f:
            json.dump(workouts_data, f, indent=2)
        print(f"✅ Embeddings saved to: {json_path}")
    
    print(f"\n✨ Demo complete!")
    print(f"\nNext steps:")
    print(f"  - View generated images in: {experiment_dir / 'demo_output'}")
    print(f"  - Compare embeddings in: {experiment_dir / 'demo_output' / 'embeddings.json'}")
    print(f"  - Train your own model: python train_model.py")
    print(f"  - Run the full web app: See main application documentation")


if __name__ == "__main__":
    main()


