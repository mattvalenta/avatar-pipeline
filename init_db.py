#!/usr/bin/env python3
"""
Initialize the avatar_videos table in Neon PostgreSQL.
Run this once before using the pipeline.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import psycopg2

load_dotenv(Path(__file__).parent / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set in .env")
    sys.exit(1)


def init_db():
    print("Connecting to Neon PostgreSQL...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True

    with conn.cursor() as cur:
        print("Creating avatar_videos table...")
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
        print("✅ Table created successfully!")

        # Create index for faster pending job lookups
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_avatar_videos_status
            ON avatar_videos(status);
        """)
        print("✅ Index created!")

    conn.close()
    print("\nDatabase initialization complete.")


if __name__ == "__main__":
    init_db()
