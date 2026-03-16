# Avatar Video Pipeline

Automated pipeline: **Script → Inworld TTS → MuseTalk on RunPod → Final Video**

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐     ┌────────────┐
│  PostgreSQL  │────▶│ Inworld TTS  │────▶│ MuseTalk/RunPod  │────▶│ Final MP4  │
│  (scripts)   │     │ (audio gen)  │     │ (lip-sync video) │     │ (output)   │
└─────────────┘     └──────────────┘     └──────────────────┘     └────────────┘
```

## Prerequisites

- Python 3.10+
- macOS (Mac mini) or Linux
- RunPod account with API key
- Inworld.ai account with API key
- Neon PostgreSQL database
- Reference face image (PNG/JPG, clear frontal face)

## Setup

### 1. Install Dependencies

```bash
cd avatar-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your actual API keys and paths
```

**Required `.env` values:**
| Variable | Description |
|----------|-------------|
| `INWORLD_API_KEY` | Your Inworld base64 API key |
| `INWORLD_VOICE_ID` | Voice ID (default: Matt's voice) |
| `RUNPOD_API_KEY` | Your RunPod API key |
| `DATABASE_URL` | Neon PostgreSQL connection string |
| `REFERENCE_IMAGE_PATH` | Path to reference face image |
| `MUSETALK_DOCKER_IMAGE` | Pre-built MuseTalk Docker image |

### 3. Set Up Database

Connect to your Neon PostgreSQL and run:

```sql
CREATE TABLE IF NOT EXISTS avatar_videos (
  id SERIAL PRIMARY KEY,
  script_text TEXT NOT NULL,
  audio_path TEXT,
  video_path TEXT,
  status TEXT DEFAULT 'pending',
  error_message TEXT,
  created_at TIMESTAMP DEFAULT NOW(),
  completed_at TIMESTAMP
);
```

### 4. Add Reference Image

Place your reference face image at the path specified in `.env`:
```bash
mkdir -p assets
cp /path/to/your/face.png assets/reference.png
```

## Usage

### Add a Script to the Database

```bash
python pipeline.py add "Hello, this is a test of the avatar video pipeline."
```

### Process a Single Script (with dry-run for testing)

```bash
python pipeline.py single "Hello world!" --dry-run
```

### Process All Pending Jobs (Batch Mode)

```bash
python pipeline.py batch --limit 5
```

### List Pending Jobs

```bash
python pipeline.py list
```

## Pipeline Steps

1. **Fetch Script** — Reads script text from the `avatar_videos` PostgreSQL table
2. **TTS Generation** — Calls Inworld TTS API to generate WAV audio (LINEAR16, 16kHz)
3. **RunPod Spin-up** — Creates a spot instance GPU pod on RunPod
4. **MuseTalk Inference** — Uploads audio + reference photo, runs lip-sync inference
5. **Download Video** — Downloads the generated MP4 video
6. **Cleanup** — Terminates the RunPod instance
7. **Update DB** — Marks job as completed, stores paths

## MuseTalk Docker Image

Build and push a pre-configured MuseTalk Docker image to avoid downloading weights on every run:

```bash
docker build -f Dockerfile.musetalk -t your-registry/musetalk:latest .
docker push your-registry/musetalk:latest
```

Update `MUSETALK_DOCKER_IMAGE` in `.env` with your image tag.

## Costs Estimate

| Service | Cost per Video |
|---------|---------------|
| Inworld TTS (Max) | ~$0.01-0.05 (depends on script length) |
| RunPod RTX 3090 (spot) | ~$0.01-0.05 (30s inference + startup) |
| Neon PostgreSQL | Free tier / negligible |
| **Total** | **~$0.02-0.10 per video** |

## Configuration Reference

### Inworld TTS Settings
- **Model:** `inworld-tts-1.5-max` (best quality)
- **Audio Format:** LINEAR16 WAV at 16kHz
- **Max text length:** 2,000 characters per request

### RunPod Settings
- **GPU:** RTX 3090 (24GB VRAM) — good price/performance
- **Cloud:** Community Cloud (spot/interruptible)
- **Disk:** 50GB container disk
- **Ports:** 22/tcp (SSH), 8888/http

### MuseTalk Settings
- **Version:** 1.5 (recommended)
- **Inference:** Single-step latent space inpainting
- **Output:** MP4 video at 25fps

## Error Handling

- Automatic retries (configurable via `MAX_RETRIES`)
- RunPod instance always terminated on failure (no orphaned pods)
- All errors logged to `pipeline.log` and stored in database
- Failed jobs marked with error message for debugging

## File Structure

```
avatar-pipeline/
├── pipeline.py           # Main pipeline script
├── requirements.txt      # Python dependencies
├── .env.example          # Environment template
├── .env                  # Actual secrets (gitignored)
├── .gitignore            # Git ignore rules
├── Dockerfile.musetalk   # MuseTalk Docker image for RunPod
├── README.md             # This file
├── assets/               # Reference images
│   └── reference.png     # Default reference face
├── audio/                # Generated TTS audio files
└── output/               # Final video outputs
```

## Security Notes

- **NEVER** commit `.env` to git — it contains API keys
- `.env` is in `.gitignore`
- API keys are loaded via environment variables
- Use `.env.example` for sharing configuration templates

## Troubleshooting

### TTS Fails
- Check `INWORLD_API_KEY` is valid
- Ensure script text is under 2,000 characters
- Check Inworld API status

### RunPod Won't Start
- Check `RUNPOD_API_KEY` is valid
- Verify GPU type is available in your region
- Try a different GPU type or region

### MuseTalk Inference Fails
- Ensure Docker image has all model weights
- Check reference image has a clear frontal face
- Verify audio file is valid WAV format
- Check pod has enough VRAM (4GB minimum)

### Database Connection Fails
- Check `DATABASE_URL` format
- Verify Neon database is active
- Check SSL settings (`sslmode=require`)

## Development

For local testing without RunPod:

```bash
# Dry run (skips RunPod, generates audio only)
python pipeline.py single "Test script" --dry-run
```

## License

Private — Paramount Lead Solutions
