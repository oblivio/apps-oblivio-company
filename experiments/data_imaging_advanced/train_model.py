"""
Training script for the Workout Autoencoder.
Run this to generate 'workout_encoder.pth'.
"""
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import pathlib

# Configuration
LATENT_DIM = 64
BATCH_SIZE = 32
EPOCHS = 50
LEARNING_RATE = 1e-3

# Reuse the model definition from actor.py (or duplicate it here for standalone running)
class Autoencoder(nn.Module):
    def __init__(self, latent_dim=LATENT_DIM):
        super(Autoencoder, self).__init__()
        # Encoder
        self.encoder = nn.Sequential(
            nn.Conv1d(3, 16, kernel_size=3, padding=1),
            nn.BatchNorm1d(16),
            nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(16, 32, kernel_size=3, padding=1),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(32, 64, kernel_size=3, padding=1),
            nn.BatchNorm1d(64),
            nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Flatten(),
            nn.Linear(64 * 8, latent_dim)
        )
        # Decoder
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

def generate_synthetic_batch(batch_size):
    """Generates random workout-like data (Batch, 3, 64)."""
    t = np.linspace(0, 2 * np.pi, 64)
    batch = []
    for _ in range(batch_size):
        # Random variations
        freq = 1.0 + np.random.rand()
        phase = np.random.rand() * 2 * np.pi
        
        # Channels: HR, Cal, Speed (normalized 0-1)
        c1 = 0.5 + 0.4 * np.sin(freq * t + phase) + np.random.rand(64) * 0.1
        c2 = 0.3 + 0.5 * np.sin(freq * 1.5 * t) + np.random.rand(64) * 0.1
        c3 = 0.4 + 0.3 * np.cos(freq * 0.5 * t) + np.random.rand(64) * 0.1
        
        batch.append(np.stack([c1, c2, c3]))
        
    return torch.tensor(np.array(batch), dtype=torch.float32)

def train():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")
    
    model = Autoencoder().to(device)
    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE)
    
    print("Training autoencoder...")
    for epoch in range(EPOCHS):
        batch = generate_synthetic_batch(BATCH_SIZE).to(device)
        
        optimizer.zero_grad()
        output = model(batch)
        loss = criterion(output, batch)
        loss.backward()
        optimizer.step()
        
        if epoch % 10 == 0:
            print(f"Epoch {epoch}/{EPOCHS}, Loss: {loss.item():.6f}")
            
    # Save model
    output_path = pathlib.Path(__file__).parent / "workout_encoder.pth"
    torch.save(model.state_dict(), output_path)
    print(f"✅ Model saved to {output_path}")

if __name__ == "__main__":
    train()

