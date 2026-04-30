---
name: avatar-pipeline
description: Generate lip-sync avatar videos using InfiniteTalk on RunPod serverless. Takes a reference face image + audio (from Inworld TTS or file) and produces a lip-synced MP4. Use when asked to: generate avatar video, create talking head video, run InfiniteTalk, use the avatar pipeline, generate TTS and video, or anything involving the avatar/talking head workflow.
---

# Avatar Pipeline

Generate lip-sync videos: **Text → Inworld TTS → InfiniteTalk (RunPod) → MP4**

## Quick Start

```bash
python3 skills/avatar-pipeline/scripts/generate_avatar.py \
  --image /path/to/reference.png \
  --text "Hello world" \
  --output /path/to/output.mp4
```

## Long Video Generation (NEW)

For scripts longer than ~20 seconds, use the intelligent chunking script:

```bash
python3 skills/avatar-pipeline/scripts/generate_long_video.py \
  --script-id 14 \
  --output /tmp/final_video.mp4

# Or with inline text:
python3 skills/avatar-pipeline/scripts/generate_long_video.py \
  --text "Your long script here..." \
  --output /tmp/final_video.mp4
```

### Intelligent Chunking
- Splits at natural sentence boundaries
- Groups ~50 words per chunk (~20 seconds)
- Handles long sentences by splitting at commas/"and"/"but"
- Skips abbreviations (Dr., Mr., etc.) to avoid false splits

### Pipeline Steps
1. **Split** - Intelligently split script at natural breakpoints
2. **TTS** - Generate Inworld TTS for each chunk
3. **Generate** - Submit each chunk to RunPod InfiniteTalk
4. **Concat** - FFmpeg concatenate all chunk videos
5. **Upload** - GCS upload of final video
6. **Update DB** - Update `video_scripts.status='complete'` with URL

> **Graphics Overlays:** MoviePy step was tested (March 19, 2026) but needs more development. Removed from pipeline for now.

## Graphics Overlays (ON HOLD)

> ⚠️ **STATUS:** MoviePy overlay step is temporarily disabled (March 19, 2026).
> Text overlays work but image overlays require OpenRouter API + more tooling.
> Pipeline ends at step 6 (GCS upload).

### Overlay Config Format (Future Use)

```json
{
  "overlays": [
    {
      "type": "text",
      "start_time": 0.0,
      "end_time": 5.0,
      "content": "Your text here",
      "position": [0.5, 0.8],
      "font_size": 36,
      "color": "white",
      "opacity": 0.9
    },
    {
      "type": "image",
      "start_time": 0.0,
      "end_time": 10.0,
      "content": "/path/to/logo.png",
      "position": [0.9, 0.1],
      "size": [0.15, 0.1]
    },
    {
      "type": "logo",
      "start_time": 0.0,
      "end_time": 999.0,
      "content": "/path/to/watermark.png",
      "position": [0.85, 0.85],
      "size": [0.1, 0.08],
      "opacity": 0.7
    }
  ]
}
```

### Overlay Types

| Type | Description | Key Params |
|------|-------------|------------|
| `text` | Text overlay | `content`, `font_size`, `color` |
| `image` | Image overlay | `content` (path), `size` |
| `logo` | Persistent logo | `content` (path), `size`, `opacity` |

### Position System
- Position is `[x_pct, y_pct]` of video size
- `[0.5, 0.5]` = center
- `[0.5, 0.9]` = bottom center
- `[0.9, 0.1]` = top right corner

### Usage

```bash
# Load overlay config from DB (stored in overlay_config column)
python3 skills/avatar-pipeline/scripts/generate_long_video.py \
  --script-id 14 \
  --output /tmp/final.mp4

# Or pass overlay config via CLI
python3 skills/avatar-pipeline/scripts/generate_long_video.py \
  --script-id 14 \
  --overlay-config '{"overlays":[{"type":"text","start_time":0,"end_time":5,"content":"TrafficDriver AI","position":[0.5,0.9]}]}' \
  --output /tmp/final.mp4

# Or via overlay file
python3 skills/avatar-pipeline/scripts/generate_long_video.py \
  --script-id 14 \
  --overlay-file /path/to/overlays.json \
  --output /tmp/final.mp4
```

### DB Schema Update

```sql
ALTER TABLE video_scripts ADD COLUMN overlay_config JSONB DEFAULT NULL;
```

### MoviePy Installation

```bash
pip install moviepy
```

## Architecture

```
Text ──▶ Inworld TTS ──▶ Audio WAV ─┐
                                     ├─▶ InfiniteTalk (RunPod) ──▶ MP4
Reference Image ─────────────────────┘
```

## Components

### RunPod InfiniteTalk Endpoint
- **Endpoint ID:** `8otp2vj31zzs4b`
- **API Key:** `RUNPOD_API_KEY` env var
- **GPU:** RTX 4090 (ADA_32_PRO), serverless
- **Image:** `us-central1-docker.pkg.dev/gen-lang-client-0254246454/infinitetalk-runpod/infinitetalk:sdpa-fix`
- **Cold start:** ~35s
- **Inference:** ~60-90s for short clips
- **Workers:** Max 3, auto-scales to 0

### Inworld TTS
- **API URL:** `https://api.inworld.ai/tts/v1/voice`
- **Auth:** `Authorization: Basic <base64 key>`
- **Voice ID (Matt):** `default-tmvxtvap_quh2wqp03bbsa__matt`
- **Model:** `inworld-tts-1.5-max`
- **Encoding:** `LINEAR16` (WAV output)

### Reference Image
- **Location:** `avatar-pipeline/assets/reference.png`
- **Requirements:** Clear frontal face, good lighting

## Input Parameters (InfiniteTalk)

| Parameter | Type | Description |
|-----------|------|-------------|
| `image_url` | string | URL to reference face image |
| `image_base64` | string | Base64 encoded face image |
| `wav_url` | string | URL to audio file (WAV/MP3) |
| `wav_base64` | string | Base64 encoded audio |
| `prompt` | string | Video description (default: "A person talking naturally") |
| `width` | int | Output width (default: 512) |
| `height` | int | Output height (default: 512) |
| `force_offload` | bool | CPU offload during inference (default: true) |

## Workflow Steps

### 1. Generate TTS Audio (Inworld)

```python
import requests, base64

resp = requests.post(
    "https://api.inworld.ai/tts/v1/voice",
    headers={
        "Content-Type": "application/json",
        "Authorization": "Basic <INWORLD_API_KEY>"
    },
    json={
        "text": "Your text here",
        "voiceId": "default-tmvxtvap_quh2wqp03bbsa__matt",
        "modelId": "inworld-tts-1.5-max",
        "audioConfig": {"audioEncoding": "LINEAR16"}
    }
)
audio_bytes = base64.b64decode(resp.json()["audioContent"])
```

### 2. Submit to InfiniteTalk

```python
import requests, base64

# Encode inputs
with open("reference.png", "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode()
with open("audio.wav", "rb") as f:
    aud_b64 = base64.b64encode(f.read()).decode()

# Submit job
resp = requests.post(
    "https://api.runpod.ai/v2/8otp2vj31zzs4b/run",
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer <RUNPOD_API_KEY>"
    },
    json={"input": {"image_base64": img_b64, "wav_base64": aud_b64}}
)
job_id = resp.json()["id"]
```

### 3. Poll for Result

```python
import time

while True:
    time.sleep(30)
    r = requests.get(
        f"https://api.runpod.ai/v2/8otp2vj31zzs4b/status/{job_id}",
        headers={"Authorization": "Bearer <RUNPOD_API_KEY>"}
    )
    d = r.json()
    if d["status"] == "COMPLETED":
        vid_b64 = d["output"]["video"]
        if vid_b64.startswith("data:"):
            vid_b64 = vid_b64.split(",", 1)[1]
        with open("output.mp4", "wb") as f:
            f.write(base64.b64decode(vid_b64))
        break
    elif d["status"] == "FAILED":
        raise Exception(d.get("error", "Unknown error"))
```

## Critical Notes

### SageAttention Fix
- RTX 4090 (CC 8.9) lacks FP8 kernel support for SageAttention
- Handler must override `attention_mode` from `"sageattn"` to `"sdpa"`
- Fix is baked into the Docker image (`sdpa-fix` tag)
- If deploying from scratch: see `mattvalenta/Infinitetalk_Runpod_hub` fork

### Base64 vs URL Input
- Base64 works for files under ~50MB (command line limits)
- For larger files, upload to a URL first or use Python (no shell arg limits)

### Cost
- ~$0.02-0.05 per 3-5 second video clip
- Serverless = pay only during inference
- ~$1.50-3/day for a few hours of actual usage

## Repository
- **Original template:** `wlsdml1114/Infinitetalk_Runpod_hub`
- **Forked (with SDPA fix):** `mattvalenta/Infinitetalk_Runpod_hub`
- **Avatar pipeline repo:** `mattvalenta/avatar-pipeline`

## Lessons Learned (March 2025)

### Image File Format
- **Convert PNG to JPG (85% quality)** before using as reference
- 2.5MB PNG → ~360KB JPG (7x smaller, much more efficient)

### Working Resolutions
| Resolution | Status | Notes |
|------------|--------|-------|
| 512x512 | ✅ Works perfectly | Fastest, safest, recommended |
| 720x1280 | ✅ Works | Requires `force_offload: true` |
| 1080x1920 | ❌ Fails | "Cannot find video" error |

### Use URL Inputs for Large Files
- Upload images/audio to GCS (e.g., `gs://recordings2134/videos/`)
- Use `image_url`/`wav_url` instead of `image_base64`/`wav_base64`
- Base64 encoding increases size by ~33%
- Large base64 files cause "body exceeded 10MB" errors on RunPod API

### Timeout Considerations
- **Endpoint timeout:** ~20 minutes
- **Short audio (<40s):** Fast, reliable
- **Long audio (80+ seconds):** Needs timeout, takes ~20-25 minutes

### Audio Size Guidelines
| Audio Duration | WAV Size | Processing Time |
|----------------|----------|----------------|
| ~30s | ~2MB | 5-8 min |
| ~80s | ~7MB | 20-25 min |

### Configuration Recommendations
- **`force_offload: true`** - Required for stability at higher resolutions
- **`force_offload: false`** - May cause memory errors at 720x1280
- **Quality:** 512x512 produces excellent results

### Long Videos: Frame Limitation & Audio Splitting

**Critical:** The InfiniteTalk model has a **~500-600 frame maximum per video generation**. At 24fps, this equals roughly **20-25 seconds** of video per segment.

**9:16 Vertical Video Recommendation:**
- **576x1024** - True 9:16 aspect ratio, safe for ~500-600 frames
- **720x1280** - Also 9:16, but higher memory usage (requires `force_offload: true`)

**For Longer Scripts (173+ seconds):**

1. **Source Script:** Query `video_scripts` table in the openclaw database:
```bash
psql "$DATABASE_URL" -c "SELECT id, script_text FROM video_scripts WHERE id = 20;"
```

2. **Split Audio at Sentences:**
   - Parse `script_text` by sentence boundaries (periods, question marks, exclamation points)
   - Group sentences into chunks under ~20-25 seconds (500-600 frames)
   - Use word count to estimate timings (~130-150 words per minute for natural speech)

3. **Generate Timestamps:**
   - Map each sentence/chunk to audio timestamps
   - Use ffmpeg to split the full TTS audio:
```bash
ffmpeg -i full_audio.wav -ss START_SECONDS -t DURATION_SECONDS -c copy chunk_1.wav
```

4. **Generate Separate Videos:**
   - Submit each chunk as a separate RunPod job
   - Save outputs as `chunk_1.mp4`, `chunk_2.mp4`, etc.

5. **Concatenate:**
```bash
ffmpeg -f concat -safe 0 -i chunks.txt -c copy final_video.mp4
```

**Audio Splitting Strategy:**
```python
import re

def split_sentences(text):
    # Split on sentence boundaries
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if s.strip()]

def group_into_chunks(sentences, words_per_chunk=50):
    # ~50 words ≈ 20 seconds at 130-150 WPM
    chunks = []
    current = []
    for sent in sentences:
        current.append(sent)
        if sum(len(c.split()) for c in current) >= words_per_chunk:
            chunks.append(" ".join(current))
            current = []
    if current:
        chunks.append(" ".join(current))
    return chunks
```

## Lessons Learned (March 19, 2026)

### Long Video Chunking - Confirmed Working
Successfully generated a ~82-second video by splitting into 6 chunks:

| Chunk | Duration | WAV Size | Video Size | Status |
|-------|----------|----------|------------|--------|
| 1 | ~30s | 1.64 MB | 3.16 MB | ✅ |
| 2 | ~27s | 1.85 MB | 3.65 MB | ✅ |
| 3 | ~18s | 1.21 MB | 2.35 MB | ✅ |
| 4 | ~22s | 1.49 MB | 2.81 MB | ✅ |
| 5 | ~17s | 1.13 MB | 2.22 MB | ✅ |
| 6 | ~10s | 0.64 MB | 1.23 MB | ✅ |

**Final concatenated video: 15 MB at 576x1024 (9:16)**

### CUDA OOM Root Cause (from RunPod logs)
The 720x1280 resolution with long audio fails due to:
```
torch.OutOfMemoryError: Allocation on device
Sampling 472 frames in 7 windows, at 1072x1920 with 6 steps
```
The VAE encoder runs out of GPU memory when processing high-res frames. Solution: use lower resolution (576x1024) or split into shorter chunks.

### Complete Long Video Workflow
```bash
# 1. Split script into chunks (manually or programmatically)
# 2. Generate TTS for each chunk
for chunk in chunks; do
  curl -X POST https://api.inworld.ai/tts/v1/voice \
    -H "Authorization: Basic $INWORLD_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"text\":\"$chunk\",\"voiceId\":\"default-tmvxtvap_quh2wqp03bbsa__matt\",\"modelId\":\"inworld-tts-1.5-max\",\"audioConfig\":{\"audioEncoding\":\"LINEAR16\"}}" \
    | jq -r '.audioContent' | base64 -d > chunk_N.wav
done

# 3. Upload to GCS
gsutil cp chunk_*.wav gs://recordings2134/videos/

# 4. Submit RunPod jobs with 576x1024 resolution
# 5. Download completed videos
# 6. Concatenate with ffmpeg
printf "file 'chunk_1.mp4'\nfile 'chunk_2.mp4'\n..." > chunks.txt
ffmpeg -f concat -safe 0 -i chunks.txt -c copy final_video.mp4
```

### Frame Limit Details
- Model processes frames in "windows" of 81 frames each
- At 720x1280: ~32 seconds per window = ~5.5 minutes per chunk
- At 512x512: ~5 seconds per window = ~1 minute per chunk
- Total max frames: ~500-600 before OOM risk

### Inworld API Key Format
- Key is Base64-encoded credential string
- Authorization header: `Basic <key>`
- Store securely - not in git repos

---

## Image Generation for Overlays

Generate overlay images (logos, backgrounds, illustrations) for use in MoviePy overlays.

### Workflow

1. **Generate with OpenRouter** - Use `google/gemini-2.5-flash-preview` model
2. **Include "green screen background" in prompt** - For easy background removal
3. **Remove background** - Use rembg or FFmpeg colorkey filter to get transparent PNG
4. **Store in GCS** - Upload to `gs://recordings2134/videos/overlays/`

### Image Types for Matt's Personal Brand

| Type | Examples |
|------|----------|
| Key phrases | Bold text overlays ("AI WORKS", "CLOSING TIME") |
| Conceptual illustrations | Brain, neural network, stack diagram, gears |
| Charts/diagrams | Funnel, growth arrows, pie charts |
| Backgrounds | Abstract geometric, automotive scenes |

### Image Generation Prompt Template

```
[Subject description], green screen background (bright green #00FF00), 
high quality, clean edges, professional lighting, 9:16 aspect ratio
```

Example prompts:
```
- "Futuristic car dashboard on green screen background"
- "Bold text 'AI PRODUCTION' on green screen"
- "Neural network brain illustration on green screen"
- "Growing bar chart on green screen background"
```

### Green Screen Removal

**Python (rembg):**
```python
from rembg import remove
from PIL import Image

input_path = "generated_image.png"
output_path = "overlay_no_bg.png"

with open(input_path, "rb") as f:
    img = Image.open(f)
    result = remove(img)
    result.save(output_path, "PNG")
```

**FFmpeg:**
```bash
ffmpeg -i input.png -vf "colorkey=0x00ff00:0.3" output.png
```

### Storage

Upload overlays to GCS:
```bash
gsutil cp overlay_*.png gs://recordings2134/videos/overlays/
```

Reference in overlay config:
```json
{
  "type": "image",
  "content": "/tmp/overlays/car_dashboard.png",
  "start_time": 0,
  "end_time": 30,
  "position": [0.5, 0.5],
  "size": [0.8, 0.8]
}
```

---

## Lessons Learned (March 19, 2026)

### MoviePy Overlays - ON HOLD
- Text overlays with MoviePy 2.x work (tested successfully on script 20)
- MoviePy 2.x has different API than 1.x - `set_start()` → `with_start()`, `set_position()` → `with_position()`
- No font support on macOS by default - must install fonts or use system fonts
- Image overlays require OpenRouter API for generation - not available in this environment
- **Decision:** Remove MoviePy from pipeline for now, focus on core avatar pipeline

### Environment Issues
- Python 3.9 installed at `/usr/bin/python3` but pip installs to `/Users/.../Library/Python/3.9`
- Use `python3` (system) not `pip` (finds wrong python)
- MoviePy 2.x import: `from moviepy import ...` not `from moviepy.editor import ...`

### Pipeline Status
- ✅ Script intake (DB)
- ✅ Intelligent chunking (`generate_long_video.py`)
- ✅ TTS (Inworld)
- ✅ Avatar video (RunPod InfiniteTalk)
- ✅ FFmpeg concat
- ✅ GCS upload
- ❌ MoviePy overlays (ON HOLD)
