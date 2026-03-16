#!/usr/bin/env python3
"""
Avatar Video Pipeline
Script → Inworld TTS → MuseTalk on RunPod → Final Video

Orchestrates the full pipeline for generating avatar videos from text scripts.
"""

import os
import sys
import json
import time
import base64
import logging
import argparse
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
INWORLD_API_KEY = os.getenv("INWORLD_API_KEY")
INWORLD_VOICE_ID = os.getenv("INWORLD_VOICE_ID", "default-tmvxtvap_quh2wqp03bbsa__matt")
INWORLD_MODEL_ID = os.getenv("INWORLD_MODEL_ID", "inworld-tts-1.5-max")
INWORLD_API_URL = "https://api.inworld.ai/tts/v1/voice"

RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY")
RUNPOD_API_URL = "https://rest.runpod.io/v1"
RUNPOD_GPU_TYPE = os.getenv("RUNPOD_GPU_TYPE", "NVIDIA GeForce RTX 3090")
RUNPOD_CONTAINER_DISK_GB = int(os.getenv("RUNPOD_CONTAINER_DISK_GB", "50"))
RUNPOD_VOLUME_GB = int(os.getenv("RUNPOD_VOLUME_GB", "0"))
RUNPOD_CLOUD_TYPE = os.getenv("RUNPOD_CLOUD_TYPE", "COMMUNITY")
RUNPOD_INTERRUPTIBLE = os.getenv("RUNPOD_INTERRUPTIBLE", "true").lower() == "true"

MUSETALK_DOCKER_IMAGE = os.getenv("MUSETALK_DOCKER_IMAGE", "runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04")

DATABASE_URL = os.getenv("DATABASE_URL")
REFERENCE_IMAGE_PATH = os.getenv("REFERENCE_IMAGE_PATH", "./assets/reference.png")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./output"))
AUDIO_DIR = Path(os.getenv("AUDIO_DIR", "./audio"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "10"))
MAX_POD_WAIT_MINUTES = int(os.getenv("MAX_POD_WAIT_MINUTES", "15"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("pipeline.log")]
)
log = logging.getLogger("avatar-pipeline")


# ===========================================================================
# Inworld TTS
# ===========================================================================
class InworldTTS:
    """Inworld TTS API client."""

    def __init__(self):
        self.api_key = INWORLD_API_KEY
        self.voice_id = INWORLD_VOICE_ID
        self.model_id = INWORLD_MODEL_ID
        self.url = INWORLD_API_URL

    def synthesize(self, text: str, output_path: Path) -> Path:
        """Generate speech audio from text. Returns path to saved WAV file."""
        if not self.api_key:
            raise ValueError("INWORLD_API_KEY not set")

        log.info(f"TTS: Synthesizing {len(text)} chars → {output_path.name}")

        headers = {
            "Authorization": f"Basic {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "voiceId": self.voice_id,
            "modelId": self.model_id,
            "audioConfig": {
                "audioEncoding": "LINEAR16",
                "sampleRateHertz": 16000,
            },
        }

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.post(self.url, json=payload, headers=headers, timeout=60)
                resp.raise_for_status()
                result = resp.json()

                audio_b64 = result["audioContent"]
                audio_bytes = base64.b64decode(audio_b64)

                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(audio_bytes)

                log.info(f"TTS: Saved {len(audio_bytes)} bytes to {output_path}")
                return output_path

            except requests.exceptions.HTTPError as e:
                log.warning(f"TTS attempt {attempt}/{MAX_RETRIES} failed: {e}")
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(2 ** attempt)
            except Exception as e:
                log.error(f"TTS unexpected error: {e}")
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(2 ** attempt)


# ===========================================================================
# RunPod Manager
# ===========================================================================
class RunPodManager:
    """Manages RunPod GPU instances for MuseTalk inference."""

    def __init__(self):
        self.api_key = RUNPOD_API_KEY
        self.base_url = RUNPOD_API_URL
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self.pod_id: Optional[str] = None

    def create_pod(self) -> str:
        """Create a new GPU pod. Returns pod ID."""
        log.info(f"RunPod: Creating pod with GPU {RUNPOD_GPU_TYPE}")

        payload = {
            "name": f"musetalk-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            "imageName": MUSETALK_DOCKER_IMAGE,
            "gpuTypeIds": [RUNPOD_GPU_TYPE],
            "gpuCount": 1,
            "containerDiskInGb": RUNPOD_CONTAINER_DISK_GB,
            "ports": ["22/tcp", "8888/http"],
            "dockerEntrypoint": ["/bin/bash"],
            "dockerStartCmd": ["-c", "sleep infinity"],
            "interruptible": RUNPOD_INTERRUPTIBLE,
            "cloudType": RUNPOD_CLOUD_TYPE,
            "env": {},
        }

        if RUNPOD_VOLUME_GB > 0:
            payload["volumeInGb"] = RUNPOD_VOLUME_GB

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/pods",
                    json=payload,
                    headers=self.headers,
                    timeout=30,
                )
                resp.raise_for_status()
                pod = resp.json()
                self.pod_id = pod["id"]
                log.info(f"RunPod: Created pod {self.pod_id}")
                return self.pod_id

            except Exception as e:
                log.warning(f"RunPod create attempt {attempt}/{MAX_RETRIES} failed: {e}")
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(5 * attempt)

    def wait_for_running(self, timeout_minutes: int = None) -> dict:
        """Wait until pod is RUNNING. Returns pod info."""
        timeout = (timeout_minutes or MAX_POD_WAIT_MINUTES) * 60
        start = time.time()

        while time.time() - start < timeout:
            pod = self.get_pod()
            status = pod.get("desiredStatus", "UNKNOWN")
            log.info(f"RunPod: Pod {self.pod_id} status: {status}")

            if status == "RUNNING":
                log.info(f"RunPod: Pod is RUNNING after {int(time.time()-start)}s")
                return pod
            elif status in ("FAILED", "EXITED", "TERMINATED"):
                raise RuntimeError(f"Pod failed with status: {status}")

            time.sleep(POLL_INTERVAL)

        raise TimeoutError(f"Pod did not start within {timeout_minutes} minutes")

    def get_pod(self) -> dict:
        """Get pod details."""
        resp = requests.get(
            f"{self.base_url}/pods/{self.pod_id}",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_pod_ip(self) -> str:
        """Get the public IP of the pod."""
        pod = self.get_pod()
        # REST API returns pod info with runtime info
        runtime = pod.get("runtime", {})
        ports = runtime.get("ports", [])
        for port in ports:
            if port.get("isIpPublic"):
                return port.get("ip")
        # Fallback
        return pod.get("machine", {}).get("podHostId", "")

    def terminate_pod(self):
        """Terminate the pod."""
        if not self.pod_id:
            return
        log.info(f"RunPod: Terminating pod {self.pod_id}")
        try:
            resp = requests.delete(
                f"{self.base_url}/pods/{self.pod_id}",
                headers=self.headers,
                timeout=30,
            )
            resp.raise_for_status()
            log.info(f"RunPod: Pod {self.pod_id} terminated")
        except Exception as e:
            log.error(f"RunPod: Failed to terminate pod: {e}")

    def stop_pod(self):
        """Stop the pod (can be resumed later)."""
        if not self.pod_id:
            return
        log.info(f"RunPod: Stopping pod {self.pod_id}")
        try:
            resp = requests.post(
                f"{self.base_url}/pods/{self.pod_id}/stop",
                headers=self.headers,
                timeout=30,
            )
            resp.raise_for_status()
            log.info(f"RunPod: Pod {self.pod_id} stopped")
        except Exception as e:
            log.error(f"RunPod: Failed to stop pod: {e}")


# ===========================================================================
# MuseTalk Runner (runs on remote RunPod via SSH)
# ===========================================================================
class MuseTalkRunner:
    """Handles MuseTalk inference on a remote RunPod instance."""

    def __init__(self, pod_manager: RunPodManager):
        self.pod = pod_manager
        self.ssh_port = 22

    def run_inference(self, audio_path: Path, image_path: Path, output_path: Path) -> Path:
        """
        Run MuseTalk inference on the remote pod.
        Uploads audio + image, runs inference, downloads video.
        """
        pod_ip = self.pod.get_pod_ip()
        if not pod_ip:
            raise RuntimeError("Could not determine pod IP address")

        log.info(f"MuseTalk: Running inference on {pod_ip}")

        # For now, we use a subprocess-based approach with scp/ssh
        # In production, you'd use paramiko or similar
        import subprocess

        ssh_base = f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -p {self.ssh_port} root@{pod_ip}"
        scp_base = f"scp -o StrictHostKeyChecking=no -o ConnectTimeout=10 -P {self.ssh_port}"

        remote_workdir = "/workspace/musetalk"
        remote_audio = f"{remote_workdir}/input_audio.wav"
        remote_image = f"{remote_workdir}/input_image.png"
        remote_output = f"{remote_workdir}/results/output.mp4"

        try:
            # Upload files
            log.info("MuseTalk: Uploading audio and image...")
            subprocess.run(
                f"{scp_base} {audio_path} root@{pod_ip}:{remote_audio}",
                shell=True, check=True, timeout=120
            )
            subprocess.run(
                f"{scp_base} {image_path} root@{pod_ip}:{remote_image}",
                shell=True, check=True, timeout=120
            )

            # Create config and run inference
            inference_script = f"""
cd {remote_workdir}
mkdir -p results
cat > configs/inference/pipeline.yaml << 'EOF'
video_path: {remote_image}
audio_path: {remote_audio}
EOF
python -m scripts.inference \\
    --inference_config configs/inference/pipeline.yaml \\
    --result_dir results \\
    --unet_model_path models/musetalkV15/unet.pth \\
    --unet_config models/musetalkV15/musetalk.json \\
    --version v15 \\
    --ffmpeg_path /usr/bin/ffmpeg
"""
            log.info("MuseTalk: Running inference on remote pod...")
            result = subprocess.run(
                f"{ssh_base} '{inference_script}'",
                shell=True, timeout=600, capture_output=True, text=True
            )
            if result.returncode != 0:
                log.error(f"MuseTalk stderr: {result.stderr}")
                raise RuntimeError(f"MuseTalk inference failed: {result.stderr[:500]}")

            # Download result
            log.info("MuseTalk: Downloading result video...")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                f"{scp_base} root@{pod_ip}:{remote_output} {output_path}",
                shell=True, check=True, timeout=120
            )

            log.info(f"MuseTalk: Video saved to {output_path}")
            return output_path

        except subprocess.TimeoutExpired:
            raise TimeoutError("MuseTalk inference timed out")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Command failed: {e}")


# ===========================================================================
# Database Manager
# ===========================================================================
class DatabaseManager:
    """PostgreSQL database operations for video jobs."""

    def __init__(self):
        self.conn = None

    def connect(self):
        self.conn = psycopg2.connect(DATABASE_URL)
        self.conn.autocommit = True
        self._ensure_table()

    def _ensure_table(self):
        with self.conn.cursor() as cur:
            cur.execute("""
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
            """)

    def get_pending_jobs(self, limit: int = 10) -> list:
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM avatar_videos WHERE status = 'pending' ORDER BY created_at ASC LIMIT %s",
                (limit,)
            )
            return cur.fetchall()

    def get_job(self, job_id: int) -> dict:
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM avatar_videos WHERE id = %s", (job_id,))
            return cur.fetchone()

    def create_job(self, script_text: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO avatar_videos (script_text, status) VALUES (%s, 'pending') RETURNING id",
                (script_text,)
            )
            return cur.fetchone()[0]

    def update_job(self, job_id: int, **kwargs):
        if not kwargs:
            return
        set_clause = ", ".join(f"{k} = %s" for k in kwargs)
        values = list(kwargs.values()) + [job_id]
        with self.conn.cursor() as cur:
            cur.execute(f"UPDATE avatar_videos SET {set_clause} WHERE id = %s", values)

    def mark_processing(self, job_id: int):
        self.update_job(job_id, status="processing")

    def mark_completed(self, job_id: int, audio_path: str, video_path: str):
        self.update_job(
            job_id,
            status="completed",
            audio_path=audio_path,
            video_path=video_path,
            completed_at=datetime.now(),
        )

    def mark_failed(self, job_id: int, error: str):
        self.update_job(job_id, status="failed", error_message=error)

    def close(self):
        if self.conn:
            self.conn.close()


# ===========================================================================
# Pipeline Orchestrator
# ===========================================================================
class AvatarPipeline:
    """Main pipeline orchestrator."""

    def __init__(self, dry_run: bool = False):
        self.tts = InworldTTS()
        self.db = DatabaseManager()
        self.dry_run = dry_run

    def process_job(self, job: dict) -> bool:
        """Process a single video job. Returns True on success."""
        job_id = job["id"]
        script_text = job["script_text"]
        log.info(f"=== Processing job {job_id} ===")
        log.info(f"Script: {script_text[:100]}...")

        audio_path = AUDIO_DIR / f"job_{job_id}.wav"
        video_path = OUTPUT_DIR / f"job_{job_id}.mp4"
        reference_image = Path(REFERENCE_IMAGE_PATH)

        if not reference_image.exists():
            if self.dry_run:
                log.info("DRY RUN: Reference image not found, creating placeholder")
                reference_image.parent.mkdir(parents=True, exist_ok=True)
                # Create a minimal valid PNG (1x1 pixel)
                import struct, zlib
                def create_minimal_png(path):
                    sig = b'\x89PNG\r\n\x1a\n'
                    ihdr_data = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
                    ihdr_crc = struct.pack('>I', zlib.crc32(b'IHDR' + ihdr_data) & 0xffffffff)
                    ihdr = struct.pack('>I', 13) + b'IHDR' + ihdr_data + ihdr_crc
                    raw = zlib.compress(b'\x00\xff\x00\x00')
                    idat_crc = struct.pack('>I', zlib.crc32(b'IDAT' + raw) & 0xffffffff)
                    idat = struct.pack('>I', len(raw)) + b'IDAT' + raw + idat_crc
                    iend_crc = struct.pack('>I', zlib.crc32(b'IEND') & 0xffffffff)
                    iend = struct.pack('>I', 0) + b'IEND' + iend_crc
                    with open(path, 'wb') as f:
                        f.write(sig + ihdr + idat + iend)
                create_minimal_png(reference_image)
            else:
                raise FileNotFoundError(f"Reference image not found: {reference_image}")

        self.db.mark_processing(job_id)

        try:
            # Step 1: Generate TTS audio
            log.info("Step 1/3: Generating TTS audio...")
            self.tts.synthesize(script_text, audio_path)

            if self.dry_run:
                log.info("DRY RUN: Skipping RunPod steps")
                self.db.mark_completed(job_id, str(audio_path), "dry-run")
                return True

            # Step 2: Run MuseTalk on RunPod
            log.info("Step 2/3: Running MuseTalk on RunPod...")
            pod_mgr = RunPodManager()
            try:
                pod_mgr.create_pod()
                pod_mgr.wait_for_running()

                muse = MuseTalkRunner(pod_mgr)
                muse.run_inference(audio_path, reference_image, video_path)
            finally:
                pod_mgr.terminate_pod()

            # Step 3: Finalize
            log.info("Step 3/3: Finalizing...")
            if not video_path.exists():
                raise FileNotFoundError(f"Video not found: {video_path}")

            self.db.mark_completed(job_id, str(audio_path), str(video_path))
            log.info(f"=== Job {job_id} completed successfully ===")
            return True

        except Exception as e:
            error_msg = str(e)
            log.error(f"Job {job_id} failed: {error_msg}")
            self.db.mark_failed(job_id, error_msg)
            return False

    def run_batch(self, limit: int = 10):
        """Process all pending jobs."""
        self.db.connect()
        try:
            jobs = self.db.get_pending_jobs(limit)
            if not jobs:
                log.info("No pending jobs found")
                return

            log.info(f"Found {len(jobs)} pending jobs")

            results = {"success": 0, "failed": 0}
            for job in jobs:
                if self.process_job(job):
                    results["success"] += 1
                else:
                    results["failed"] += 1

            log.info(f"Batch complete: {results['success']} succeeded, {results['failed']} failed")
        finally:
            self.db.close()

    def process_single(self, script_text: str) -> Optional[str]:
        """Process a single script and return the video path."""
        self.db.connect()
        try:
            job_id = self.db.create_job(script_text)
            job = self.db.get_job(job_id)
            if self.process_job(job):
                # Re-fetch to get updated paths
                updated_job = self.db.get_job(job_id)
                return updated_job.get("video_path")
            return None
        finally:
            self.db.close()


# ===========================================================================
# CLI
# ===========================================================================
def main():
    parser = argparse.ArgumentParser(description="Avatar Video Pipeline")
    sub = parser.add_subparsers(dest="command", help="Command")

    # Batch processing
    batch_parser = sub.add_parser("batch", help="Process pending jobs from DB")
    batch_parser.add_argument("--limit", type=int, default=10, help="Max jobs to process")

    # Single script
    single_parser = sub.add_parser("single", help="Process a single script")
    single_parser.add_argument("script", help="Script text to process")
    single_parser.add_argument("--dry-run", action="store_true", help="Skip RunPod steps")

    # Add job to DB
    add_parser = sub.add_parser("add", help="Add a job to the database")
    add_parser.add_argument("script", help="Script text")

    # List jobs
    sub.add_parser("list", help="List pending jobs")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "batch":
        pipeline = AvatarPipeline()
        pipeline.run_batch(args.limit)

    elif args.command == "single":
        pipeline = AvatarPipeline(dry_run=args.dry_run)
        video_path = pipeline.process_single(args.script)
        if video_path:
            print(f"✅ Video saved: {video_path}")
        else:
            print("❌ Pipeline failed")
            sys.exit(1)

    elif args.command == "add":
        db = DatabaseManager()
        db.connect()
        try:
            job_id = db.create_job(args.script)
            print(f"✅ Job created with ID: {job_id}")
        finally:
            db.close()

    elif args.command == "list":
        db = DatabaseManager()
        db.connect()
        try:
            jobs = db.get_pending_jobs(100)
            if not jobs:
                print("No pending jobs")
            for job in jobs:
                print(f"  [{job['id']}] {job['status']} - {job['script_text'][:80]}...")
        finally:
            db.close()


if __name__ == "__main__":
    main()
