#!/usr/bin/env python3
"""
Avatar Pipeline: Long Video Generator with Intelligent Chunking + MoviePy Overlays

Takes a full script, intelligently splits at natural breakpoints,
generates avatar videos for each chunk, concatenates, applies graphics overlays, uploads to GCS.

Usage:
    python3 generate_long_video.py --text "Your long script..." --output final.mp4
    python3 generate_long_video.py --script-id 14 --output final.mp4
    python3 generate_long_video.py --script-id 14 --overlay-config '{"overlays":[...]}' --output final.mp4
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

import requests

# Try to import MoviePy - install if needed
try:
    from moviepy.editor import (
        VideoFileClip, AudioFileClip, ImageClip, CompositeVideoClip,
        concatenate_videoclips, ColorClip, TextClip
    )
    MOVIEPY_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False
    print("Warning: MoviePy not installed. Run: pip install moviepy")


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
GCS_BUCKET = "gs://recordings2134/videos"
DB_URL = os.getenv("DATABASE_URL", "")

# Video generation params
WIDTH = 576
HEIGHT = 1024  # 9:16 vertical
FORCE_OFFLOAD = True
MAX_WORDS_PER_CHUNK = 50  # ~20 seconds at 130-150 WPM


@dataclass
class Chunk:
    index: int
    text: str
    audio_path: str = ""
    video_path: str = ""
    job_id: Optional[str] = None
    status: str = "pending"


@dataclass
class OverlayConfig:
    """Configuration for a single overlay element."""
    type: str  # "text", "image", "logo", "shape"
    start_time: float  # seconds
    end_time: float  # seconds
    content: str = ""  # text content or image path
    position: tuple = (0.5, 0.5)  # (x_pct, y_pct) - center is (0.5, 0.5)
    size: tuple = (0.3, 0.1)  # (width_pct, height_pct) of video
    color: str = "white"
    font_size: int = 48
    font: str = "DejaVu-Sans"
    opacity: float = 1.0
    animation: str = "none"  # "none", "fade_in", "slide_up"


@dataclass
class OverlayScene:
    """Full overlay configuration for a video."""
    overlays: list = field(default_factory=list)


class IntelligentSplitter:
    """Split scripts at natural breakpoints for smooth video transitions."""
    
    END_PUNCT = r'[.!?]'
    TRANSITION_PATTERNS = [
        r'\bNow\b', r'\bBut\b', r'\bSo\b', r'\bHere\b', r'\bLet\b',
        r'\bFirst\b', r'\bSecond\b', r'\bThird\b', r'\bFinally\b',
        r'\bThat\b', r'\bThis\b', r'\bWhat\b', r'\bWhen\b', r'\bHow\b',
        r'\bAnd\b', r'\bBecause\b', r'\bHowever\b', r'\bTherefore\b',
    ]
    
    @classmethod
    def split(cls, text: str, max_words: int = MAX_WORDS_PER_CHUNK) -> list[str]:
        text = re.sub(r'\s+', ' ', text).strip()
        sentences = cls._split_into_sentences(text)
        chunks = cls._group_sentences(sentences, max_words)
        return chunks
    
    @classmethod
    def _split_into_sentences(cls, text: str) -> list[str]:
        text = re.sub(r'\bDr\.\s', 'Dr._BREAK_', text)
        text = re.sub(r'\bMr\.\s', 'Mr._BREAK_', text)
        text = re.sub(r'\bMrs\.\s', 'Mrs._BREAK_', text)
        text = re.sub(r'\bMs\.\s', 'Ms._BREAK_', text)
        text = re.sub(r'\bvs\.\s', 'vs._BREAK_', text)
        text = re.sub(r'\be\.g\.\s', 'e.g._BREAK_', text)
        text = re.sub(r'\bi\.e\.\s', 'i.e._BREAK_', text)
        
        parts = re.split(rf'({cls.END_PUNCT}+)\s+', text)
        
        sentences = []
        current = ""
        for i, part in enumerate(parts):
            current += part
            if i % 2 == 1:
                continue
            if i % 2 == 0 and i > 0:
                if current.strip():
                    sentences.append(current.strip())
                current = ""
        
        if current.strip():
            sentences.append(current.strip())
        
        sentences = [s.replace('_BREAK_', '.') for s in sentences]
        return [s for s in sentences if s.strip()]
    
    @classmethod
    def _group_sentences(cls, sentences: list[str], max_words: int) -> list[str]:
        chunks = []
        current_chunk = []
        current_words = 0
        
        for sent in sentences:
            sent_words = len(sent.split())
            
            if sent_words > max_words * 1.5:
                sub_chunks = cls._split_long_sentence(sent, max_words)
                for sub in sub_chunks:
                    if current_chunk:
                        chunks.append(" ".join(current_chunk))
                        current_chunk = []
                        current_words = 0
                    chunks.append(sub)
                continue
            
            if current_words + sent_words > max_words and current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = []
                current_words = 0
            
            current_chunk.append(sent)
            current_words += sent_words
        
        if current_chunk:
            chunks.append(" ".join(current_chunk))
        
        return chunks
    
    @classmethod
    def _split_long_sentence(cls, sentence: str, max_words: int) -> list[str]:
        split_pattern = r'(?:,\s*|\s+and\s+|\s+but\s+|\s+or\s+|\s*;\s*)'
        parts = re.split(split_pattern, sentence)
        
        chunks = []
        current = []
        current_words = 0
        
        for part in parts:
            part_words = len(part.split())
            if current_words + part_words > max_words and current:
                chunks.append(" ".join(current))
                current = [part]
                current_words = part_words
            else:
                current.append(part)
                current_words += part_words
        
        if current:
            chunks.append(" ".join(current))
        
        final_chunks = []
        for chunk in chunks:
            words = chunk.split()
            while len(words) > max_words:
                final_chunks.append(" ".join(words[:max_words]))
                words = words[max_words:]
            if words:
                final_chunks.append(" ".join(words))
        
        return final_chunks


class TTSGenerator:
    """Generate TTS audio via Inworld API."""
    
    @classmethod
    def generate(cls, text: str, output_path: str) -> str:
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
        
        print(f"  TTS: {len(audio_bytes):,} bytes -> {output_path}")
        return output_path


class RunPodRunner:
    """Submit and monitor RunPod InfiniteTalk jobs."""
    
    @classmethod
    def submit(cls, image_path: str, audio_path: str) -> str:
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
                    "prompt": "A person talking naturally",
                    "width": WIDTH,
                    "height": HEIGHT,
                    "force_offload": FORCE_OFFLOAD
                }
            }
        )
        resp.raise_for_status()
        job_id = resp.json().get("id")
        print(f"  Job: {job_id}")
        return job_id
    
    @classmethod
    def wait(cls, job_id: str, output_path: str, poll_interval: int = 30, max_wait: int = 1800) -> str:
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
                print(f"  Done: {len(vid_bytes):,} bytes -> {output_path}")
                return output_path
            
            elif status == "FAILED":
                raise Exception(f"Job failed: {d.get('error', 'Unknown error')}")
            
            print(f"  {elapsed}s - {status}")
        
        raise TimeoutError(f"Job {job_id} did not complete within {max_wait}s")


class FFmpegConcat:
    """Concatenate videos using FFmpeg."""
    
    @classmethod
    def concat(cls, chunk_paths: list[str], output_path: str) -> str:
        if len(chunk_paths) == 1:
            shutil.copy(chunk_paths[0], output_path)
            return output_path
        
        concat_file = output_path.replace(".mp4", "_chunks.txt")
        with open(concat_file, "w") as f:
            for path in chunk_paths:
                f.write(f"file '{path}'\n")
        
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            output_path
        ]
        
        print(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise Exception(f"FFmpeg concat failed: {result.stderr}")
        
        os.unlink(concat_file)
        print(f"  Concatenated {len(chunk_paths)} chunks -> {output_path}")
        return output_path


class MoviePyOverlay:
    """Apply graphics overlays to video using MoviePy."""
    
    @classmethod
    def apply_overlays(cls, video_path: str, overlay_config: dict, output_path: str) -> str:
        """
        Apply overlay_config JSON to video and save as output_path.
        
        overlay_config format:
        {
            "overlays": [
                {
                    "type": "text",
                    "start_time": 0.0,
                    "end_time": 5.0,
                    "content": "Your text here",
                    "position": (0.5, 0.8),  # center-bottom
                    "font_size": 36,
                    "color": "white",
                    "opacity": 0.9
                },
                {
                    "type": "image",
                    "start_time": 0.0,
                    "end_time": 10.0,
                    "content": "/path/to/logo.png",
                    "position": (0.9, 0.1),  # top-right
                    "size": (0.15, 0.1)  # 15% width, 10% height
                },
                {
                    "type": "logo",
                    "start_time": 0.0,
                    "end_time": 999.0,  # until end
                    "content": "/path/to/watermark.png",
                    "position": (0.85, 0.85),
                    "size": (0.1, 0.08),
                    "opacity": 0.7
                }
            ]
        }
        """
        if not MOVIEPY_AVAILABLE:
            raise ImportError("MoviePy not installed. Run: pip install moviepy")
        
        print(f"  Loading video: {video_path}")
        video = VideoFileClip(video_path)
        video_duration = video.duration
        
        clips = [video]
        
        for ov in overlay_config.get("overlays", []):
            ov_type = ov.get("type", "")
            start = max(0, ov.get("start_time", 0))
            end = min(video_duration, ov.get("end_time", video_duration))
            
            if start >= end or start >= video_duration:
                continue
            
            duration = end - start
            
            if ov_type == "text":
                txt = ov.get("content", "")
                pos = ov.get("position", (0.5, 0.5))
                font_size = ov.get("font_size", 36)
                color = ov.get("color", "white")
                opacity = ov.get("opacity", 1.0)
                
                # Create text clip
                txt_clip = TextClip(
                    txt,
                    fontsize=font_size,
                    color=color,
                    font="DejaVu-Sans",
                    method="caption",
                    size=(video.w * 0.8, None)
                ).set_start(start).set_duration(duration).set_opacity(opacity)
                
                # Position: convert percentage to pixels
                x = int(pos[0] * video.w - txt_clip.w / 2)
                y = int(pos[1] * video.h - txt_clip.h / 2)
                txt_clip = txt_clip.set_position((max(0, x), max(0, y)))
                
                clips.append(txt_clip)
                print(f"  Added text overlay: '{txt[:30]}...' at {start}-{end}s")
                
            elif ov_type == "image":
                img_path = ov.get("content", "")
                if not os.path.exists(img_path):
                    print(f"  Warning: Image not found: {img_path}")
                    continue
                
                pos = ov.get("position", (0.5, 0.5))
                size_pct = ov.get("size", (0.2, 0.2))
                opacity = ov.get("opacity", 1.0)
                
                # Calculate size in pixels
                img_w = int(size_pct[0] * video.w)
                img_h = int(size_pct[1] * video.h)
                
                img_clip = ImageClip(img_path).resize(width=img_w, height=img_h)
                img_clip = img_clip.set_start(start).set_duration(duration).set_opacity(opacity)
                
                x = int(pos[0] * video.w - img_w / 2)
                y = int(pos[1] * video.h - img_h / 2)
                img_clip = img_clip.set_position((max(0, x), max(0, y)))
                
                clips.append(img_clip)
                print(f"  Added image overlay: {img_path} at {start}-{end}s")
                
            elif ov_type == "logo":
                img_path = ov.get("content", "")
                if not os.path.exists(img_path):
                    print(f"  Warning: Logo not found: {img_path}")
                    continue
                
                pos = ov.get("position", (0.9, 0.9))
                size_pct = ov.get("size", (0.1, 0.1))
                opacity = ov.get("opacity", 0.7)
                
                img_w = int(size_pct[0] * video.w)
                img_h = int(size_pct[1] * video.h)
                
                img_clip = ImageClip(img_path).resize(width=img_w, height=img_h)
                img_clip = img_clip.set_start(start).set_duration(duration).set_opacity(opacity)
                
                x = int(pos[0] * video.w - img_w / 2)
                y = int(pos[1] * video.h - img_h / 2)
                img_clip = img_clip.set_position((max(0, x), max(0, y)))
                
                clips.append(img_clip)
                print(f"  Added logo overlay: {img_path} at {start}-{end}s")
        
        # Composite
        print(f"  Compositing {len(clips)} layers...")
        final = CompositeVideoClip(clips, size=video.size)
        final = final.set_duration(video_duration)
        
        # Write output
        print(f"  Rendering to {output_path}...")
        final.write_videofile(
            output_path,
            codec='libx264',
            audio=False,
            preset='ultrafast',
            verbose=False,
            logger=None
        )
        
        # Cleanup
        video.close()
        final.close()
        for clip in clips:
            clip.close()
        
        print(f"  Overlay complete: {output_path}")
        return output_path


class GCSUploader:
    """Upload files to Google Cloud Storage."""
    
    @classmethod
    def upload(cls, local_path: str, gcs_path: str = None) -> str:
        if gcs_path is None:
            gcs_path = f"{GCS_BUCKET}/{Path(local_path).name}"
        
        cmd = ["gcloud", "storage", "cp", local_path, gcs_path]
        print(f"  Uploading: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise Exception(f"GCS upload failed: {result.stderr}")
        
        public_url = gcs_path.replace("gs://", "https://storage.googleapis.com/")
        print(f"  Uploaded: {public_url}")
        return public_url


class DatabaseUpdater:
    """Update video_scripts table in Neon PostgreSQL."""
    
    @classmethod
    def update(cls, script_id: int, video_url: str, status: str = "complete"):
        try:
            import psycopg2
            
            match = re.match(r'postgresql://([^:]+):([^@]+)@(.+)/(.+)', DB_URL)
            if not match:
                raise ValueError(f"Invalid DB URL format")
            
            user, password, host, dbname = match.groups()
            host = host.split('?')[0]
            
            conn = psycopg2.connect(
                host=host,
                user=user,
                password=password,
                database=dbname,
                sslmode='require'
            )
            
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE video_scripts 
                SET video_url = %s, status = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (video_url, status, script_id)
            )
            conn.commit()
            cur.close()
            conn.close()
            
            print(f"  DB updated: id={script_id}, status={status}")
        except Exception as e:
            print(f"  DB update failed: {e}")


def generate_long_video(
    script_text: str,
    output_path: str,
    reference_image: str = DEFAULT_IMAGE,
    script_id: int = None,
    overlay_config: dict = None
) -> dict:
    """
    Main pipeline: split -> TTS -> generate -> concat -> overlay -> upload -> update DB
    """
    print(f"\n{'='*60}")
    print(f"GENERATING LONG VIDEO")
    print(f"{'='*60}")
    print(f"Script length: {len(script_text.split())} words")
    print(f"Output: {output_path}")
    print(f"Reference: {reference_image}")
    if overlay_config:
        print(f"Overlays: {len(overlay_config.get('overlays', []))} elements")
    print()
    
    # Step 1: Intelligent split
    print("STEP 1: Intelligent Script Splitting")
    print("-" * 40)
    chunks_text = IntelligentSplitter.split(script_text)
    print(f"Split into {len(chunks_text)} chunks:")
    for i, chunk in enumerate(chunks_text):
        word_count = len(chunk.split())
        print(f"  Chunk {i+1}: {word_count} words")
    print()
    
    temp_dir = Path(output_path).parent / f"chunks_{int(time.time())}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    chunks = []
    video_paths = []
    
    # Step 2: Generate TTS + Video for each chunk
    print("STEP 2: Generate TTS + Videos")
    print("-" * 40)
    
    for i, chunk_text in enumerate(chunks_text):
        chunk = Chunk(
            index=i + 1,
            text=chunk_text,
            audio_path=str(temp_dir / f"chunk_{i+1:02d}.wav"),
            video_path=str(temp_dir / f"chunk_{i+1:02d}.mp4")
        )
        chunks.append(chunk)
        
        print(f"\nChunk {chunk.index}/{len(chunks_text)}:")
        print(f"  Text: {chunk.text[:80]}...")
        
        try:
            TTSGenerator.generate(chunk.text, chunk.audio_path)
        except Exception as e:
            print(f"  TTS FAILED: {e}")
            continue
        
        try:
            chunk.job_id = RunPodRunner.submit(reference_image, chunk.audio_path)
        except Exception as e:
            print(f"  Submit FAILED: {e}")
            continue
    
    # Step 3: Wait for all jobs
    print("\n" + "=" * 60)
    print("STEP 3: Wait for Videos")
    print("-" * 40)
    
    for chunk in chunks:
        if not chunk.job_id:
            continue
        
        print(f"\nChunk {chunk.index}/{len(chunks)} (job: {chunk.job_id}):")
        try:
            RunPodRunner.wait(chunk.job_id, chunk.video_path)
            video_paths.append(chunk.video_path)
        except Exception as e:
            print(f"  Video FAILED: {e}")
            continue
    
    if not video_paths:
        raise Exception("No videos generated successfully")
    
    # Step 4: Concatenate
    print("\n" + "=" * 60)
    print("STEP 4: Concatenate Videos")
    print("-" * 40)
    merged_path = output_path.replace(".mp4", "_merged.mp4")
    FFmpegConcat.concat(video_paths, merged_path)
    
    file_size = os.path.getsize(merged_path)
    print(f"Merged video: {file_size:,} bytes ({file_size/1024/1024:.1f} MB)")
    
    # Step 5: Apply overlays (NEW)
    final_path = output_path
    if overlay_config and overlay_config.get("overlays"):
        print("\n" + "=" * 60)
        print("STEP 5: Apply MoviePy Overlays")
        print("-" * 40)
        if not MOVIEPY_AVAILABLE:
            print("  Warning: MoviePy not available, skipping overlays")
            final_path = merged_path
        else:
            try:
                MoviePyOverlay.apply_overlays(merged_path, overlay_config, final_path)
            except Exception as e:
                print(f"  Overlay FAILED: {e}, using unoverlay version")
                final_path = merged_path
    else:
        # No overlays, just rename merged to final
        shutil.copy(merged_path, final_path)
    
    # Step 6: Upload to GCS
    print("\n" + "=" * 60)
    print("STEP 6: Upload to GCS")
    print("-" * 40)
    try:
        gcs_url = GCSUploader.upload(final_path)
    except Exception as e:
        print(f"GCS upload failed: {e}")
        gcs_url = None
    
    # Step 7: Update database
    if script_id and gcs_url:
        print("\n" + "=" * 60)
        print("STEP 7: Update Database")
        print("-" * 40)
        DatabaseUpdater.update(script_id, gcs_url)
    
    final_size = os.path.getsize(final_path)
    print("\n" + "=" * 60)
    print("DONE!")
    print(f"Final video: {final_path} ({final_size:,} bytes)")
    if gcs_url:
        print(f"Public URL: {gcs_url}")
    print("=" * 60)
    
    return {
        "output_path": final_path,
        "gcs_url": gcs_url,
        "chunks": len(chunks),
        "successful_chunks": len(video_paths),
        "file_size": final_size,
        "has_overlays": bool(overlay_config and overlay_config.get("overlays"))
    }


def main():
    parser = argparse.ArgumentParser(description="Generate long avatar video with intelligent chunking")
    parser.add_argument("--text", help="Full script text (alternative to --script-id)")
    parser.add_argument("--script-id", type=int, help="Video script ID from database")
    parser.add_argument("--output", default="/tmp/final_video.mp4", help="Output video path")
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="Reference face image")
    parser.add_argument("--overlay-config", help="JSON string for overlay configuration")
    parser.add_argument("--overlay-file", help="JSON file containing overlay configuration")
    parser.add_argument("--max-words", type=int, default=MAX_WORDS_PER_CHUNK, help="Max words per chunk")
    
    args = parser.parse_args()
    
    # Load overlay config
    overlay_config = None
    if args.overlay_config:
        overlay_config = json.loads(args.overlay_config)
    elif args.overlay_file and os.path.exists(args.overlay_file):
        with open(args.overlay_file) as f:
            overlay_config = json.load(f)
    
    # Get script text
    if args.script_id:
        print(f"Fetching script {args.script_id} from database...")
        try:
            import psycopg2
            match = re.match(r'postgresql://([^:]+):([^@]+)@(.+)/(.+)', DB_URL)
            user, password, host, dbname = match.groups()
            host = host.split('?')[0]
            
            conn = psycopg2.connect(
                host=host, user=user, password=password, database=dbname, sslmode='require'
            )
            cur = conn.cursor()
            cur.execute(
                "SELECT script_text, overlay_config FROM video_scripts WHERE id = %s",
                (args.script_id,)
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            
            if not row:
                print(f"Script {args.script_id} not found")
                sys.exit(1)
            
            script_text = row[0]
            db_overlay_config = row[1]
            
            # Use DB overlay_config if not provided via CLI
            if db_overlay_config and not overlay_config:
                if isinstance(db_overlay_config, str):
                    overlay_config = json.loads(db_overlay_config)
                else:
                    overlay_config = db_overlay_config
            
            print(f"Loaded script: {len(script_text.split())} words")
            if overlay_config:
                print(f"Loaded overlay config: {len(overlay_config.get('overlays', []))} elements")
        except Exception as e:
            print(f"Database error: {e}")
            sys.exit(1)
    elif args.text:
        script_text = args.text
    else:
        parser.error("Provide --text or --script-id")
    
    if args.max_words:
        global MAX_WORDS_PER_CHUNK
        MAX_WORDS_PER_CHUNK = args.max_words
    
    result = generate_long_video(
        script_text=script_text,
        output_path=args.output,
        reference_image=args.image,
        script_id=args.script_id,
        overlay_config=overlay_config
    )
    
    print(f"\nResult: {json.dumps(result, indent=2)}")


if __name__ == "__main__":
    main()
