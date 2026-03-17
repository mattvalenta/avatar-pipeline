#!/bin/bash
# Entrypoint with two modes:
# - MODE=cmdserver (default): starts HTTP command server
# - MODE=normal: runs original logic (download models, keep alive)

MODE=${MODE:-cmdserver}

if [ "$MODE" = "normal" ]; then
    # Original logic: download models if not present
    if [ ! -f "/workspace/models/musetalkV15/unet.pth" ]; then
        echo "First run: downloading model weights..."
        bash /app/download_models.sh
    fi
    echo "MuseTalk v1.5 ready!"
    echo "Models cached in: /workspace/models/"
    echo "Run inference with: python3 -m scripts.inference ..."
    tail -f /dev/null
else
    # Command server mode - starts HTTP server for remote control
    echo "Starting HTTP command server on port 8080..."
    python3 /app/cmdserver.py
fi
