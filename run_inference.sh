#!/bin/bash
# Run MuseTalk inference
# Usage: bash run_inference.sh <audio_path> <image_path> <output_path>

AUDIO_PATH="${1}"
IMAGE_PATH="${2}"
OUTPUT_PATH="${3:-/app/output/result.mp4}"

if [ -z "$AUDIO_PATH" ] || [ -z "$IMAGE_PATH" ]; then
    echo "Usage: bash run_inference.sh <audio_path> <image_path> [output_path]"
    exit 1
fi

python3 -m scripts.inference \
    --inference_config ./configs/inference/test.yaml \
    --result_dir ./results/output \
    --unet_model_path /workspace/models/musetalkV15/unet.pth \
    --unet_config /workspace/models/musetalkV15/musetalk.json \
    --sd_model_name /workspace/models/hf_cache/models--runwayml--stable-diffusion-v1-5 \
    --vae_model_name /workspace/models/hf_cache/models--stabilityai--sd-vae-ft-mse \
    --audio_path "$AUDIO_PATH" \
    --image_path "$IMAGE_PATH" \
    --output_path "$OUTPUT_PATH"
