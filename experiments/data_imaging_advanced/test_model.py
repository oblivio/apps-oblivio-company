#!/usr/bin/env python3
"""Quick test to verify the model works correctly"""

import torch
import numpy as np
import pathlib

# Load the model
model_path = pathlib.Path(__file__).parent / "workout_encoder.pth"
print(f"Loading model from: {model_path}")

if not model_path.exists():
    print(f"❌ Model file not found at {model_path}")
    exit(1)

# Load state dict
state_dict = torch.load(model_path, map_location='cpu')
print(f"✅ Model loaded. Keys: {list(state_dict.keys())[:5]}...")

# Create the encoder architecture
from train_model import Autoencoder

model = Autoencoder(latent_dim=64)
encoder = model.encoder

# Load weights
encoder.load_state_dict(state_dict)
encoder.eval()

# Test with random input
test_input = torch.randn(1, 3, 64)
print(f"Test input shape: {test_input.shape}")
print(f"Test input range: [{test_input.min():.4f}, {test_input.max():.4f}]")

with torch.no_grad():
    output = encoder(test_input)

print(f"Output shape: {output.shape}")
print(f"Output range: [{output.min():.4f}, {output.max():.4f}]")
print(f"Output mean: {output.mean():.4f}")
print(f"Output std: {output.std():.4f}")

if torch.allclose(output, torch.zeros_like(output)):
    print("❌ ERROR: Model returns all zeros!")
    exit(1)
else:
    print("✅ Model works correctly - produces non-zero output")
    print(f"First 10 values: {output.squeeze(0).numpy()[:10]}")

