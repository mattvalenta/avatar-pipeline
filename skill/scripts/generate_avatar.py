#!/usr/bin/env python3
"""
Avatar Pipeline: Generate lip-sync videos via InfiniteTalk + Inworld TTS

Usage:
    python3 generate_avatar.py --image reference.png --text "Hello world" --output video.mp4
    python3 generate_avatar.py --image reference.png --audio voice.wav --output video.mp4
"""

import argparse
import base64
import json
import os
import sys
import time

import requests

# Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
DEFAULT_IMAGE = os.path.join(SKILL_DIR, "assets", "reference.png")
RUNPOD_ENDPOINT = "https://api.runpod.ai/v2/8otp2vj31zzs4b"
RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY", "")
INWORLD_API_URL = "https://api.inworld.ai/tts/v1/voice"
INWORLD_API_KEY = os.getenv("INWORLD_API_KEY", "")
INWORLD_VOICE_ID = os.getenv("INWORLD_VOICE_ID", "default-tmvxtvap_quh2wqp03bbsa__matt")
INWORLD_MODEL_ID = os.getenv("INWORLD_MODEL_ID", "inworld-tts-1.5-max")


def generate_tts(text: str, output_path: str) -> str:
    """Generate TTS audio via Inworld. Returns path to saved WAV."""
    if not INWORLD_API_KEY:
        raise ValueError("INWORLD_API_KEY not set")
    
    resp = requests.post(
        INWORLD_API_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {INWORLD_API_KEY}"
        },
        json={
            "text": text,
            "voiceId": INWORLD_VOICE_ID,
            "modelId": INWORLD_MODEL_ID,
            "audioConfig": {"audioEncoding": "LINEAR16"}
        }
    )
    resp.raise_for_status()
    
    audio_b64 = resp.json().get("audioContent")
    if not audio_b64:
        raise Exception(f"TTS failed: {resp.json()}")
    
    audio_bytes = base64.b64decode(audio_b64)
    with open(output_path, "wb") as f:
        f.write(audio_bytes)
    
    print(f"TTS: Saved {len(audio_bytes)} bytes to {output_path}")
    return output_path


def run_infinitetalk(image_path: str, audio_path: str, prompt: str = "A person talking naturally",
                      width: int = 512, height: int = 512, force_offload: bool = True) -> str:
    """Submit job to InfiniteTalk. Returns job ID."""
    
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    with open(audio_path, "rb") as f:
        aud_b64 = base64.b64encode(f.read()).decode()
    
    resp = requests.post(
        f"{RUNPOD_ENDPOINT}/run",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {RUNPOD_API_KEY}"
        },
        json={
            "input": {
                "image_base64": img_b64,
                "wav_base64": aud_b64,
                "prompt": prompt,
                "width": width,
                "height": height,
                "force_offload": force_offload
            }
        }
    )
    resp.raise_for_status()
    job_id = resp.json().get("id")
    print(f"InfiniteTalk: Job submitted - {job_id}")
    return job_id


def wait_for_result(job_id: str, output_path: str, poll_interval: int = 30, max_wait: int = 600) -> str:
    """Poll for job completion. Returns output path."""
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval
        
        r = requests.get(
            f"{RUNPOD_ENDPOINT}/status/{job_id}",
            headers={"Authorization": f"Bearer {RUNPOD_API_KEY}"}
        )
        d = r.json()
        status = d.get("status")
        
        if status == "COMPLETED":
            vid_b64 = d.get("output", {}).get("video", "")
            if vid_b64.startswith("data:"):
                vid_b64 = vid_b64.split(",", 1)[1]
            
            vid_bytes = base64.b64decode(vid_b64)
            with open(output_path, "wb") as f:
                f.write(vid_bytes)
            print(f"Video saved: {len(vid_bytes)} bytes to {output_path}")
            return output_path
        
        elif status == "FAILED":
            raise Exception(f"Job failed: {d.get('error', 'Unknown error')}")
        
        print(f"  {elapsed}s - {status}")
    
    raise TimeoutError(f"Job did not complete within {max_wait}s")


def main():
    parser = argparse.ArgumentParser(description="Generate avatar video")
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="Reference face image path")
    parser.add_argument("--text", help="Text for TTS (uses Inworld)")
    parser.add_argument("--audio", help="Pre-generated audio WAV path (skip TTS)")
    parser.add_argument("--output", default="output.mp4", help="Output video path")
    parser.add_argument("--prompt", default="A person talking naturally", help="Video prompt")
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=512)
    args = parser.parse_args()
    
    # Step 1: Get audio
    if args.audio:
        audio_path = args.audio
        print(f"Using audio: {audio_path}")
    elif args.text:
        audio_path = args.output.replace(".mp4", "_audio.wav")
        generate_tts(args.text, audio_path)
    else:
        parser.error("Provide --text or --audio")
    
    # Step 2: Run InfiniteTalk
    job_id = run_infinitetalk(args.image, audio_path, args.prompt, args.width, args.height)
    
    # Step 3: Wait for result
    wait_for_result(job_id, args.output)
    
    print(f"\nDone! Video: {args.output}")


if __name__ == "__main__":
    main()
