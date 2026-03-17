#!/bin/bash
# Download MuseTalk model weights to /workspace/models/ (persistent volume)
# Run once on first startup, models are cached thereafter

MODEL_DIR="/workspace/models"
HF_BASE="https://huggingface.co/TMElyralab/MuseTalk/resolve/main"

mkdir -p "$MODEL_DIR/musetalkV15"

echo "Checking MuseTalk model weights..."
if [ ! -f "$MODEL_DIR/musetalkV15/unet.pth" ]; then
    echo "Downloading MuseTalk v1.5 weights (~1.5GB)..."
    wget -q --show-progress "$HF_BASE/musetalkV15/unet.pth" -O "$MODEL_DIR/musetalkV15/unet.pth"
    wget -q "$HF_BASE/musetalkV15/musetalk.json" -O "$MODEL_DIR/musetalkV15/musetalk.json"
fi

echo "Checking Stable Diffusion 1.5..."
if [ ! -d "$MODEL_DIR/hf_cache/models--runwayml--stable-diffusion-v1-5" ]; then
    echo "Downloading Stable Diffusion 1.5 (~4GB)..."
    HF_HOME="$MODEL_DIR/hf_cache" python3 -c "from huggingface_hub import snapshot_download; snapshot_download('runwayml/stable-diffusion-v1-5')"
fi

echo "Checking SD VAE..."
if [ ! -d "$MODEL_DIR/hf_cache/models--stabilityai--sd-vae-ft-mse" ]; then
    echo "Downloading SD VAE (~350MB)..."
    HF_HOME="$MODEL_DIR/hf_cache" python3 -c "from huggingface_hub import snapshot_download; snapshot_download('stabilityai/sd-vae-ft-mse')"
fi

echo "All models ready!"
