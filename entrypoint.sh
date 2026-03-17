#!/bin/bash
# Entrypoint: download models on first run, then keep container alive

# Download models if not present
if [ ! -f "/workspace/models/musetalkV15/unet.pth" ]; then
    echo "First run: downloading model weights..."
    bash /app/download_models.sh
fi

echo "MuseTalk v1.5 ready!"
echo "Models cached in: /workspace/models/"
echo "Run inference with: bash /app/run_inference.sh <audio> <image> [output]"

# Keep container running
tail -f /dev/null
