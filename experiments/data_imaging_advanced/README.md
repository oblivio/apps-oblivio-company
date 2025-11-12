# Data Imaging Advanced - Model Generation

This experiment uses a PyTorch autoencoder model to generate 64-dimensional embeddings for workout time series data.

## Model Generation (Offline)

The model **must be generated offline** (outside Docker) before building the Docker image or running the application.

### Prerequisites

Install the required dependencies:

```bash
pip install torch numpy
```

Or install all dependencies from the requirements file:

```bash
pip install -r experiments/data_imaging_advanced/requirements.txt
```

### Generating the Model

Run the training script to generate the model:

```bash
python experiments/data_imaging_advanced/train_model.py \
  --output-path experiments/data_imaging_advanced/workout_encoder.pth \
  --epochs 50 \
  --batch-size 32
```

**Default location**: The model will be saved to `experiments/data_imaging_advanced/workout_encoder.pth` by default.

### Training Options

- `--output-path`: Path where the model will be saved (default: `workout_encoder.pth`)
- `--epochs`: Number of training epochs (default: 50)
- `--batch-size`: Batch size for training (default: 32)
- `--learning-rate`: Learning rate (default: 0.001)
- `--num-samples`: Number of synthetic training samples (default: 2000)

### Docker Usage

1. **Generate the model offline** (as described above)
2. **Build the Docker image** - the model file will be included automatically:
   ```bash
   docker build -t your-image-name .
   ```
3. **Run with docker-compose** - the model will be available in the container:
   ```bash
   docker-compose up
   ```

The model file is automatically included in the Docker image when it exists in the `experiments/data_imaging_advanced/` directory.

### Custom Model Path

You can specify a custom model path using the `WORKOUT_ENCODER_MODEL_PATH` environment variable:

```bash
export WORKOUT_ENCODER_MODEL_PATH=/path/to/your/model.pth
```

### Troubleshooting

If you see an error about the model file not being found:

1. Verify the model file exists: `ls -la experiments/data_imaging_advanced/workout_encoder.pth`
2. Check the path in the error message
3. Regenerate the model if needed using the command above

