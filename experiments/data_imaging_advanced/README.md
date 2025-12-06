# Advanced Workout Autoencoder Experiment

This experiment takes the "Data Imaging" concept further by using a **trained PyTorch Autoencoder** to generate 64-dimensional learned embeddings, rather than the manual 192-dimensional "visual flattening" used in the basic experiment.

## Key Differences

| Feature | Original (`data_imaging`) | Advanced (`data_imaging_advanced`) |
|---------|---------------------------|------------------------------------|
| **Embedding** | 192-dim Flattened Image | **64-dim Learned Latent Vector** |
| **Method** | Manual Feature Engineering | **Deep Learning (Autoencoder)** |
| **Logic** | Hardcoded logic in `engine.py` | PyTorch Model in `actor.py` |
| **Search** | Finds visually similar grids | **Finds non-linear pattern correlations** |

## How It Works

1.  **Encoder**: The system uses a 1D Convolutional Neural Network (CNN) to compress the 3-channel time-series data (Heart Rate, Calories, Speed) into a dense 64-float vector.
2.  **Latent Space**: This vector represents the "essence" of the workout in a way that captures complex relationships (e.g., "high speed but low heart rate").
3.  **Search**: MongoDB Atlas Vector Search finds "Workout Twins" based on this learned semantic similarity.

## Usage

### 1. Auto-Loading
On first visit, the gallery automatically generates 10 workouts using the actor's synthetic data generator and runs them through the Autoencoder.

### 2. Manual Generation
Click **"Generate New Workout"** to create a single new entry.

### 3. Training
To regenerate the model weights (`workout_encoder.pth`), run the training script:

```bash
python experiments/data_imaging_advanced/train_model.py
```

This ensures the actor uses a model trained on fresh synthetic data patterns.

## File Structure

- `actor.py`: Contains the `Autoencoder` class definition and the logic to load/run it inside a Ray actor.
- `train_model.py`: Standalone script to train the model and save weights.
- `workout_encoder.pth`: The saved PyTorch model weights.
- `manifest.json`: Configures the 64-dim vector index.

