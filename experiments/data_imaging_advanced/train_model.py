#!/usr/bin/env python3
"""
Training script for the PyTorch autoencoder used in data_imaging_advanced.

This script:
1. Generates synthetic workout data for training
2. Trains an autoencoder to compress 64-point time series into 64-dim embeddings
3. Saves the encoder weights to workout_encoder.pth

Usage:
    python train_model.py [--output-path OUTPUT_PATH] [--epochs EPOCHS] [--batch-size BATCH_SIZE]
"""

import argparse
import pathlib
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Configuration matching actor.py
LATENT_DIM = 64
NORM_BOUNDS = {
    "heart_rate": (50, 200),
    "calories_per_min": (0, 20),
    "speed_kph": (0, 15),
}


class Autoencoder(nn.Module):
    """Autoencoder architecture matching actor.py"""
    
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
        
        # Decoder
        self.decoder = nn.Sequential(
            nn.Linear(latent_dim, 64 * 8), # (B, 512)
            nn.ReLU(),
            nn.Unflatten(1, (64, 8)), # (B, 64, 8)
            nn.Conv1d(64, 64, kernel_size=3, padding=1), # (B, 64, 8)
            nn.BatchNorm1d(64),
            nn.ReLU(),
            nn.Upsample(scale_factor=2), # (B, 64, 16)
            nn.Conv1d(64, 32, kernel_size=3, padding=1), # (B, 32, 16)
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.Upsample(scale_factor=2), # (B, 32, 32)
            nn.Conv1d(32, 16, kernel_size=3, padding=1), # (B, 16, 32)
            nn.BatchNorm1d(16),
            nn.ReLU(),
            nn.Upsample(scale_factor=2), # (B, 16, 64)
            nn.Conv1d(16, 3, kernel_size=3, padding=1), # (B, 3, 64)
            nn.Sigmoid()
        )
    
    def forward(self, x):
        encoded = self.encoder(x)
        decoded = self.decoder(encoded)
        return decoded


class WorkoutDataset(Dataset):
    """Dataset for synthetic workout time series data"""
    
    def __init__(self, num_samples=1000, seed=42):
        self.num_samples = num_samples
        np.random.seed(seed)
        self.data = self._generate_data()
    
    def _normalize(self, data, min_val, max_val):
        """Normalize to [0, 1] range"""
        clipped = np.clip(data, min_val, max_val)
        range_val = max_val - min_val
        if range_val == 0:
            return np.zeros_like(clipped, dtype=np.float32)
        normalized = (clipped - min_val) / range_val
        return normalized.astype(np.float32)
    
    def _generate_data(self):
        """Generate synthetic workout time series"""
        data = []
        t = np.linspace(0, 2 * np.pi, 64)
        
        for i in range(self.num_samples):
            # Generate varied patterns
            hr_base = 100 + (i % 7) * 5
            cal_base = 5 + (i % 5) * 1
            speed_base = 3.5 + (i % 6) * 0.5
            
            # Create different patterns based on index
            pattern_type = i % 4
            
            if pattern_type == 0:  # Steady state
                hr = hr_base + 20 * np.sin(t) + np.random.randn(64) * 5
                cal = cal_base + 2 * np.sin(t) + np.random.randn(64) * 0.5
                speed = speed_base + np.random.randn(64) * 0.3
            elif pattern_type == 1:  # Interval training
                hr = hr_base + 60 * np.sin(t * 4) + np.random.randn(64) * 10
                cal = cal_base + 4 * np.sin(t * 4) + np.random.randn(64) * 1
                speed = speed_base * (np.sin(t * 4) > 0) + np.random.randn(64) * 0.5
            elif pattern_type == 2:  # Progressive ramp
                hr = hr_base + 40 * t / (2 * np.pi) + np.random.randn(64) * 8
                cal = cal_base + 3 * t / (2 * np.pi) + np.random.randn(64) * 0.8
                speed = speed_base + 2 * t / (2 * np.pi) + np.random.randn(64) * 0.4
            else:  # Recovery
                hr = hr_base - 30 * t / (2 * np.pi) + np.random.randn(64) * 6
                cal = cal_base - 2 * t / (2 * np.pi) + np.random.randn(64) * 0.6
                speed = speed_base * 0.7 + np.random.randn(64) * 0.2
            
            # Ensure values are within bounds
            hr = np.clip(hr, NORM_BOUNDS["heart_rate"][0], NORM_BOUNDS["heart_rate"][1])
            cal = np.clip(cal, NORM_BOUNDS["calories_per_min"][0], NORM_BOUNDS["calories_per_min"][1])
            speed = np.clip(speed, NORM_BOUNDS["speed_kph"][0], NORM_BOUNDS["speed_kph"][1])
            
            # Normalize
            hr_norm = self._normalize(hr, *NORM_BOUNDS["heart_rate"])
            cal_norm = self._normalize(cal, *NORM_BOUNDS["calories_per_min"])
            speed_norm = self._normalize(speed, *NORM_BOUNDS["speed_kph"])
            
            # Stack into (3, 64) format
            sample = np.stack([hr_norm, cal_norm, speed_norm], axis=0)
            data.append(sample)
        
        return np.array(data, dtype=np.float32)
    
    def __len__(self):
        return len(self.data)
    
    def __getitem__(self, idx):
        return torch.from_numpy(self.data[idx])


def train_model(
    output_path: str = "workout_encoder.pth",
    epochs: int = 50,
    batch_size: int = 32,
    learning_rate: float = 0.001,
    num_samples: int = 2000
):
    """Train the autoencoder model"""
    
    logger.info(f"Starting training with {num_samples} samples, {epochs} epochs, batch_size={batch_size}")
    
    # Create dataset and dataloader
    dataset = WorkoutDataset(num_samples=num_samples)
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
    
    # Initialize model
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Using device: {device}")
    
    model = Autoencoder(latent_dim=LATENT_DIM).to(device)
    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=learning_rate)
    
    # Training loop
    model.train()
    for epoch in range(epochs):
        total_loss = 0.0
        num_batches = 0
        
        for batch_data in dataloader:
            batch_data = batch_data.to(device)
            
            # Forward pass
            optimizer.zero_grad()
            reconstructed = model(batch_data)
            loss = criterion(reconstructed, batch_data)
            
            # Backward pass
            loss.backward()
            optimizer.step()
            
            total_loss += loss.item()
            num_batches += 1
        
        avg_loss = total_loss / num_batches if num_batches > 0 else 0.0
        
        if (epoch + 1) % 10 == 0 or epoch == 0:
            logger.info(f"Epoch [{epoch+1}/{epochs}], Average Loss: {avg_loss:.6f}")
    
    # Save only the encoder part (as used in actor.py)
    logger.info(f"Saving encoder to {output_path}")
    output_file = pathlib.Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    torch.save(model.encoder.state_dict(), output_path)
    logger.info(f"✅ Model saved successfully to {output_path}")
    
    # Verify the saved model can be loaded
    test_model = Autoencoder(latent_dim=LATENT_DIM)
    test_model.encoder.load_state_dict(torch.load(output_path, map_location='cpu'))
    logger.info("✅ Model verification: Successfully loaded saved encoder")
    
    return model


def main():
    parser = argparse.ArgumentParser(description="Train PyTorch autoencoder for workout embeddings")
    parser.add_argument(
        "--output-path",
        type=str,
        default="workout_encoder.pth",
        help="Path to save the trained encoder model (default: workout_encoder.pth)"
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=50,
        help="Number of training epochs (default: 50)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Batch size for training (default: 32)"
    )
    parser.add_argument(
        "--learning-rate",
        type=float,
        default=0.001,
        help="Learning rate (default: 0.001)"
    )
    parser.add_argument(
        "--num-samples",
        type=int,
        default=2000,
        help="Number of synthetic training samples (default: 2000)"
    )
    
    args = parser.parse_args()
    
    train_model(
        output_path=args.output_path,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        num_samples=args.num_samples
    )


if __name__ == "__main__":
    main()

