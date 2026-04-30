# Avatar Pipeline — Quick Start

**This repo contains everything needed to generate lip-sync avatar videos.**

## Pipeline

```
Text → Inworld TTS → InfiniteTalk (RunPod) → MP4 → GCS → DB Update
```

## Quick Start

1. **Read the full knowledge base:** `skill/AGENTS.md` — comprehensive docs, all gotchas, all infrastructure details
2. **Read the skill docs:** `skill/SKILL.md` — script parameters, code examples, overlay config
3. **Set up environment:** Copy `.env.example` → `.env` and fill in your API keys

## Generate a Video

```bash
# Single clip
python3 skill/scripts/generate_avatar.py \
  --image skill/assets/reference.png \
  --text "Hello world" \
  --output /tmp/output.mp4

# Long video with automatic chunking
python3 skill/scripts/generate_long_video.py \
  --text "Your full script here..." \
  --output /tmp/final.mp4
```

## Repo Structure

| Path | Description |
|------|-------------|
| `skill/AGENTS.md` | Full knowledge base (read this first) |
| `skill/SKILL.md` | Skill documentation with API examples |
| `skill/scripts/` | Python scripts (generate_avatar.py, generate_long_video.py) |
| `skill/assets/` | Reference face image |
| `pipeline.py` | Legacy pipeline script |
| `Dockerfile.musetalk` | MuseTalk Docker image spec |
| `.env.example` | Environment template (copy to .env) |

## Key Rules

1. Set `INWORLD_API_KEY` and `RUNPOD_API_KEY` in `.env` before running
2. Resolution: 576x1024 (9:16 vertical) — higher resolutions cause CUDA OOM
3. Max ~20-25 seconds per chunk (500-600 frame limit)
4. Always use `force_offload: true`
5. See `skill/AGENTS.md` for complete documentation

---

*Read `skill/AGENTS.md` for the full knowledge base.*
