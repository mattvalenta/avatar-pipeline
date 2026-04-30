# AVATAR PIPELINE — Agent Knowledge Base

**Pipeline:** Text → Inworld TTS → InfiniteTalk (RunPod Serverless) → MP4 → FFmpeg Concat → GCS Upload → DB Update

This document gives any agent everything needed to operate, maintain, and extend the avatar video generation pipeline.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Infrastructure & Credentials](#infrastructure--credentials)
3. [Scripts & Usage](#scripts--usage)
4. [How The Pipeline Works](#how-the-pipeline-works)
5. [Database Schema](#database-schema)
6. [RunPod Infrastructure](#runpod-infrastructure)
7. [Lessons Learned & Gotchas](#learned--gotchas)
8. [MoviePy Overlays (ON HOLD)](#moviepy-overlays-on-hold)
9. [Mission Control App Integration](#mission-control-app-integration)
10. [References](#references)

---

## Architecture Overview

```
Text Script
    │
    ▼
[1] Intelligent Chunking (generate_long_video.py)
    │  Split at sentence boundaries, ~50 words/chunk (~20s)
    │
    ▼
[2] Inworld TTS (per chunk)
    │  POST https://api.inworld.ai/tts/v1/voice
    │  Voice: Matt's voice | Model: inworld-tts-1.5-max
    │
    ▼
[3] InfiniteTalk via RunPod (per chunk)
    │  Endpoint: 8otp2vj31zzs4b
    │  GPU: RTX 4090 | Resolution: 576x1024 (9:16)
    │
    ▼
[4] FFmpeg Concatenation
    │  Merge all chunk MP4s into final video
    │
    ▼
[5] GCS Upload
    │  gs://recordings2134/videos/
    │
    ▼
[6] DB Update
    Neon PostgreSQL → video_scripts.status='complete'
```

---

## Infrastructure & Credentials

> **Security Note:** All API keys should be stored as environment variables, NOT hardcoded in source files.
> Real keys belong in `.env` files (git-ignored) or secret managers.

### RunPod

| Item | Value |
|------|-------|
| **Endpoint ID** | `8otp2vj31zzs4b` |
| **API Key** | `RUNPOD_API_KEY` env var (fallback: hardcoded in scripts — sanitize before making repo public) |
| **GPU** | RTX 4090 (Ada Lovelace, CC 8.9) |
| **Container Image** | `us-central1-docker.pkg.dev/gen-lang-client-0254246454/infinitetalk-runpod/infinitetalk:sdpa-fix` |
| **Artifact Registry** | `us-central1-docker.pkg.dev/gen-lang-client-0254246454/infinitetalk-runpod` (public, `allUsers` → `roles/artifactregistry.reader`) |
| **Cold Start** | ~35 seconds |
| **Inference Time** | ~60-90s per short clip |
| **Max Workers** | 3, auto-scales to 0 |
| **Cost** | ~$0.02-0.05 per 3-5s clip |

### Inworld TTS

| Item | Value |
|------|-------|
| **API URL** | `https://api.inworld.ai/tts/v1/voice` |
| **Auth** | `Authorization: Basic <base64_key>` |
| **API Key** | `INWORLD_API_KEY` env var |
| **Voice ID (Matt)** | `default-tmvxtvap_quh2wqp03bbsa__matt` |
| **Model** | `inworld-tts-1.5-max` |
| **Audio Encoding** | `LINEAR16` (WAV output, 16-bit PCM, 16kHz mono) |

### Reference Image

| Item | Value |
|------|-------|
| **Path** | `skill/assets/reference.png` (also `avatar-pipeline/assets/reference.png`) |
| **Original** | 1215x1620 JPEG, ~555KB |
| **Requirements** | Clear frontal face, good lighting |

### GCS (Google Cloud Storage)

| Item | Value |
|------|-------|
| **Bucket** | `gs://recordings2134/videos/` |
| **Public Access** | Bucket has uniform access (no individual ACLs) |

### Database

| Item | Value |
|------|-------|
| **Provider** | Neon PostgreSQL |
| **Database** | `openclaw` |
| **Table** | `video_scripts` |
| **Host** | `ep-dry-mountain-ae3fsqlh-pooler.c-2.us-east-2.aws.neon.tech` |

---

## Scripts & Usage

All scripts are in `skill/scripts/`.

### generate_avatar.py — Single Clip Generation

Generates a single avatar video from text or audio file.

```bash
# From text (generates TTS first)
python3 skill/scripts/generate_avatar.py \
  --image skill/assets/reference.png \
  --text "Hello world" \
  --output /tmp/output.mp4

# From pre-generated audio
python3 skill/scripts/generate_avatar.py \
  --image skill/assets/reference.png \
  --audio /path/to/audio.wav \
  --output /tmp/output.mp4
```

**Parameters:**
- `--image` — Reference face image (default: `skill/assets/reference.png`)
- `--text` — Text for TTS generation (alternative to `--audio`)
- `--audio` — Pre-generated WAV file (skips TTS)
- `--output` — Output MP4 path (default: `output.mp4`)
- `--prompt` — Video description prompt (default: "A person talking naturally")
- `--width` / `--height` — Output resolution (default: 512x512)

### generate_long_video.py — Long Video with Chunking

Full pipeline script: chunking → TTS per chunk → RunPod per chunk → concat → GCS → DB update.

```bash
# From database script ID
python3 skill/scripts/generate_long_video.py \
  --script-id 14 \
  --output /tmp/final_video.mp4

# From inline text
python3 skill/scripts/generate_long_video.py \
  --text "Your long script here..." \
  --output /tmp/final_video.mp4

# With overlay config
python3 skill/scripts/generate_long_video.py \
  --script-id 14 \
  --overlay-config '{"overlays":[{"type":"text","start_time":0,"end_time":5,"content":"Title"}]}' \
  --output /tmp/final_video.mp4
```

**Default chunk settings:**
- Width: 576, Height: 1024 (9:16 vertical)
- Max ~50 words per chunk (~20 seconds at 130-150 WPM)
- `force_offload: true`
- Splits at sentence boundaries (`.`, `!`, `?`)
- Long sentences split at commas, `and`, `but`

---

## How The Pipeline Works

### Step 1: Script Chunking

The `generate_long_video.py` script's `split_into_chunks()` function:

1. Splits text on sentence boundaries: `re.split(r'(?<=[.!?])\s+', text)`
2. Groups sentences into chunks of ~50 words
3. If a single sentence exceeds ~50 words, splits at commas, `and`, `but`
4. Skips abbreviations (Dr., Mr., etc.)

**Critical constraint:** InfiniteTalk has a ~500-600 frame max per job. At 24fps, that's ~20-25 seconds. Exceeding this causes CUDA OOM.

### Step 2: TTS Generation (Inworld)

```python
resp = requests.post(
    "https://api.inworld.ai/tts/v1/voice",
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Basic {INWORLD_API_KEY}"
    },
    json={
        "text": chunk_text,
        "voiceId": "default-tmvxtvap_quh2wqp03bbsa__matt",
        "modelId": "inworld-tts-1.5-max",
        "audioConfig": {"audioEncoding": "LINEAR16"}
    }
)
audio_bytes = base64.b64decode(resp.json()["audioContent"])
# → save as WAV file
```

### Step 3: InfiniteTalk (RunPod)

```python
resp = requests.post(
    "https://api.runpod.ai/v2/8otp2vj31zzs4b/run",
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {RUNPOD_API_KEY}"
    },
    json={
        "input": {
            "image_base64": img_b64,
            "wav_base64": aud_b64,
            "prompt": "A person talking naturally",
            "width": 576,
            "height": 1024,
            "force_offload": True
        }
    }
)
job_id = resp.json()["id"]
```

### Step 4: Poll for Completion

```python
while True:
    time.sleep(30)
    r = requests.get(
        f"https://api.runpod.ai/v2/8otp2vj31zzs4b/status/{job_id}",
        headers={"Authorization": f"Bearer {RUNPOD_API_KEY}"}
    )
    d = r.json()
    if d["status"] == "COMPLETED":
        vid_b64 = d["output"]["video"]
        # Strip data: prefix if present
        if vid_b64.startswith("data:"):
            vid_b64 = vid_b64.split(",", 1)[1]
        with open("output.mp4", "wb") as f:
            f.write(base64.b64decode(vid_b64))
        break
    elif d["status"] == "FAILED":
        raise Exception(d.get("error", "Unknown error"))
```

### Step 5: FFmpeg Concatenation

```python
# Create chunks.txt:
# file 'chunk_1.mp4'
# file 'chunk_2.mp4'
# ...

ffmpeg -f concat -safe 0 -i chunks.txt -c copy final_video.mp4
```

### Step 6: GCS Upload

```bash
gsutil cp final_video.mp4 gs://recordings2134/videos/
# Public URL: https://storage.googleapis.com/recordings2134/videos/<filename>
```

### Step 7: Update Database

```sql
UPDATE video_scripts 
SET status = 'complete', 
    video_url = 'https://storage.googleapis.com/recordings2134/videos/<filename>',
    updated_at = NOW()
WHERE id = <script_id>;
```

---

## Database Schema

The `video_scripts` table on Neon PostgreSQL:

```sql
CREATE TABLE video_scripts (
    id SERIAL PRIMARY KEY,
    script_text TEXT NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',  -- pending, processing, complete, failed
    video_url VARCHAR(500),
    overlay_config JSONB DEFAULT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

**Column notes:**
- `status`: Tracks progress through the pipeline
- `video_url`: Public GCS URL of the final video
- `overlay_config`: JSONB for MoviePy overlays (currently ON HOLD)

### Query scripts from DB

```bash
psql "$DATABASE_URL" \
  -c "SELECT id, status, video_url FROM video_scripts ORDER BY id DESC LIMIT 10;"
```

---

## RunPod Infrastructure

### InfiniteTalk (wlsdml1114/infinitetalk)

InfiniteTalk is a lip-sync model that takes a reference face image + audio and produces a talking-head video.

**How it works internally:**
- Uses ComfyUI under the hood with a custom handler
- Handler connects to local ComfyUI via WebSocket at `127.0.0.1`
- Workflow nodes: LoadImage (node 284), LoadAudio (node 125), WanVideoSampler (nodes 128/320)
- Handler uploads files via ComfyUI's `/upload/image` and `/upload/audio` endpoints

### SDPA Fix (Critical)

**Problem:** SageAttention FP8 kernels crash on RTX 4090 (CC 8.9):
```
CUDA error: no kernel image is available for execution on the device
sageattention/quant.py line 283, per_channel_fp8
```

**Fix:** Override `attention_mode` from `"sageattn"` to `"sdpa"` in the WanVideoModelLoader node. SDPA (Scaled Dot Product Attention) works on all GPUs, ~10-15% slower but stable.

**Fixed in:** `mattvalenta/Infinitetalk_Runpod_hub` fork (commit `3116c2c`)
**Fixed image:** `infinitetalk:sdpa-fix` on GCP Artifact Registry

### Resolution Guide

| Resolution | Status | Notes |
|------------|--------|-------|
| 512x512 | ✅ Safest | ~5s per window, fastest |
| 576x1024 | ✅ Recommended | True 9:16, safe for ~500-600 frames |
| 720x1280 | ⚠️ Risky | Requires `force_offload: true`, ~32s per window |
| 1080x1920 | ❌ Fails | CUDA OOM, "Cannot find video" error |

**Frame limit:** ~500-600 frames max. At 24fps = ~20-25 seconds per chunk.
**Window size:** Model processes 81 frames per window.

### Timing Reference

| Audio Duration | WAV Size | Processing Time | Video Size |
|----------------|----------|-----------------|------------|
| ~20s | ~1MB | ~5-8 min | ~3 MB |
| ~30s | ~2MB | ~8-12 min | ~3-4 MB |
| ~50s | ~3MB | ~15-20 min | ~5-8 MB |
| ~80s | ~7MB | ~20-25 min | ~10+ MB |
| 80s+ | ❌ | CUDA OOM | — |

---

## Learned & Gotchas

### 1. Always Use force_offload: true
Required for stability at 576x1024 and above. Without it, CUDA memory errors are likely.

### 2. Base64 vs URL Input
- Base64 works for files under ~50MB
- For larger files, upload to GCS first and use `image_url`/`wav_url` instead of `image_base64`/`wav_base64`
- Base64 increases size by ~33%
- RunPod API has ~10MB body limit for `/run` endpoint

### 3. PNG → JPG Conversion
Convert PNG reference images to JPG (85% quality) before use:
- 2.5MB PNG → ~360KB JPG (7x smaller)
- Faster upload, less memory

### 4. Docker Image Architecture Pattern
**Docker image = environment, NOT data.** Model weights (6GB+) download at runtime.
- `Dockerfile.musetalk` = system deps + Python packages (~4GB)
- `download_models.sh` = first-run model fetch to `/workspace/models/`
- Benefits: faster builds (~2 min vs ~2+ hours), smaller images, model updates without rebuilds

### 5. ARM64 Cannot Build AMD64 CUDA Images Reliably
- Mac mini (ARM64) cross-compilation with `docker buildx` is unreliable for large images
- **GitHub Actions CI (14GB disk)** is insufficient for 9GB+ images
- **GCP Cloud Build (100GB disk)** is the reliable path for building MuseTalk images
- Use `docker buildx build --load` for local testing only

### 6. ghcr.io Image Must Be Public
RunPod cannot pull private images. If deploying a new image:
- Make the GitHub Package public: Settings → Packages → musetalk → Make public
- Or use GCP Artifact Registry with public access (allUsers → roles/artifactregistry.reader)

### 7. RunPod GraphQL API Limitations
- Does NOT expose container logs (need web console for debugging)
- Template introspection disabled
- `podTemplates` only returns system templates, not user templates
- Pods stuck with 0 uptime + null ports = image pull failure

### 8. MoviePy 2.x API Changes
If re-enabling MoviePy overlays:
- `from moviepy import ...` NOT `from moviepy.editor import ...`
- `with_start()` NOT `set_start()`
- `with_position()` NOT `set_position()`

### 9. Python Environment on Mac Mini
- System Python 3.9 at `/usr/bin/python3`
- pip installs to user library path
- Use `python3` (system) not `pip` for consistency

### 10. Security: Never Commit API Keys
- All API keys must be in `.env` files (git-ignored) or environment variables
- Assume all repos could become public
- Current scripts have fallback hardcoded keys — sanitize before making public

---

## MoviePy Overlays (ON HOLD)

**Status:** Disabled March 19, 2026. Text overlays work, image overlays blocked by missing OpenRouter API.

### DB Schema for Overlays
```sql
ALTER TABLE video_scripts ADD COLUMN overlay_config JSONB DEFAULT NULL;
```

### Overlay Config Format
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
    }
  ]
}
```

- **Position system:** `[x_pct, y_pct]` of video size. `[0.5, 0.5]` = center.
- **Types:** `text`, `image`, `logo`
- Install: `pip install moviepy`

---

## Mission Control App Integration

The pipeline is integrated into the Mission Control app at `https://github.com/mattvalenta/mission-control.git`.

### MC App Routes
- `GET/POST /api/social/video-scripts` — List/create scripts
- `GET/PATCH /api/social/video-scripts/[id]` — Get/update script
- `POST /api/social/video-scripts/generate` — Trigger video generation

### MC App Components
- `src/components/social/VideoScriptPanel.tsx` — Script CRUD UI + status display
- `src/app/api/social/video-scripts/route.ts` — API route handler
- `src/app/api/social/video-scripts/generate/route.ts` — Generation trigger

### Database Migration
- `migrations/012-video-scripts.sql` — Creates `video_scripts` table
- MC App dev server runs on **port 4000** (not 3000)

---

## References

### Repositories

| Repo | URL | Purpose |
|------|-----|---------|
| **avatar-pipeline** | `https://github.com/mattvalenta/avatar-pipeline` | Main pipeline (this repo) |
| **InfiniteTalk (original)** | `wlsdml1114/Infinitetalk_Runpod_hub` | RunPod template source |
| **InfiniteTalk (fork)** | `mattvalenta/Infinitetalk_Runpod_hub` | SDPA attention fix |
| **mission-control** | `https://github.com/mattvalenta/mission-control.git` | MC App with video script UI |

### Docker Images

| Image | Location | Size | Notes |
|-------|----------|------|-------|
| **infinitetalk:sdpa-fix** | GCP Artifact Registry | ~4GB | SDPA fix for RTX 4090 |
| **musetalk:latest** | ghcr.io/mattvalenta/avatar-pipeline/musetalk | 9.31GB | Env only, models at runtime |
| **museTalk:v15** | ghcr.io | 9.31GB | Prior version |

### GCP Details

| Resource | Value |
|----------|-------|
| **Project** | `gen-lang-client-0254246454` |
| **Artifact Registry** | `us-central1-docker.pkg.dev/gen-lang-client-0254246454/infinitetalk-runpod` |

### File Manifest (This Repo)

```
avatar-pipeline/
├── skill/
│   ├── AGENTS.md                         # This file — agent knowledge base
│   ├── SKILL.md                          # Detailed skill documentation
│   ├── scripts/
│   │   ├── generate_avatar.py            # Single video generation
│   │   └── generate_long_video.py        # Full pipeline with chunking
│   ├── assets/
│   │   └── reference.png                 # Matt's face reference image
│   └── references/                       # (empty, for future docs)
├── pipeline.py                           # Legacy/main pipeline script
├── init_db.py                            # Database initialization
├── Dockerfile.musetalk                   # MuseTalk Docker image for RunPod
├── download_models.sh                    # First-run model download
├── entrypoint.sh                         # Docker entrypoint
├── entrypoint_new.sh                     # Updated entrypoint
├── cmdserver.py                          # HTTP command server for pods
├── run_inference.sh                      # MuseTalk inference runner
├── requirements.txt                      # Python dependencies
├── README.md                             # Project README
├── .env.example                          # Environment template
├── .env                                  # Actual env (git-ignored)
└── .github/workflows/build-musetalk.yml  # CI for Docker builds
```

---

*Last updated: 2026-04-30*
*Pipeline status: OPERATIONAL*
*Last test: Video Script #29 ("Vibe Coding") — 12 chunks, all successful*
