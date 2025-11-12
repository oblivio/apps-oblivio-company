#!/bin/bash
# Helper script to generate the PyTorch model offline
# Usage: ./generate_model.sh [output_path]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_PATH="${1:-${SCRIPT_DIR}/workout_encoder.pth}"

echo "Generating PyTorch model for data_imaging_advanced..."
echo "Output path: ${OUTPUT_PATH}"

python "${SCRIPT_DIR}/train_model.py" \
  --output-path "${OUTPUT_PATH}" \
  --epochs 50 \
  --batch-size 32

echo ""
echo "✅ Model generated successfully at: ${OUTPUT_PATH}"
echo ""
echo "You can now build your Docker image and the model will be included."

