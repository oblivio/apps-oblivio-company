# Data Imaging Experiment (Basic)

This experiment demonstrates **Visual Fingerprinting** for workout analysis. It converts time-series data into images to find similar workouts.

## How It Works

1.  **Normalization**: Heart rate, calories, and speed are normalized to 0-255.
2.  **Folding**: 1D arrays (64 mins) are reshaped into 2D grids (8x8).
3.  **Stacking**: These grids form the R, G, B channels of an image.
4.  **Embedding**: The 8x8x3 image is flattened into a **192-dimensional vector**.

This "hardcoded" vector is used to find "Workout Twins" – sessions that *look* the same visually.

## Quick Start

1.  **Start the Platform**:
    ```bash
    docker-compose up
    ```
2.  **Visit the Gallery**:
    [https://localhost:5001/experiments/data_imaging/](https://localhost:5001/experiments/data_imaging/)

3.  **Generate Data**:
    - Click **"Generate Demo"** to create 100 synthetic workouts.
    - Click **"Generate"** to create one.

## File Structure

- `actor.py`: Logic for data generation and visualization.
- `engine.py`: Core image processing functions.
- `manifest.json`: Configures the 192-dim vector index.

## Note

This is the **Basic** version using manual feature engineering. 
For the Machine Learning approach (Autoencoder), see `experiments/data_imaging_advanced/`.
