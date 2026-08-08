"""
AI Photo-to-Video App — Backend API
FastAPI service handling uploads, job creation, and status polling.
Uses Supabase Storage (not S3) and Upstash Redis (TLS/rediss://).
Free-to-use app: enforces a per-device daily generation cap to control GPU spend.
"""

import os
import uuid
import time
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from celery import Celery
import redis
from supabase import create_client, Client

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]          # the secret/service_role key
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "ai-video-maker")

REDIS_URL = os.environ["REDIS_URL"]                # starts with rediss:// (Upstash, TLS)
DAILY_FREE_LIMIT = int(os.environ.get("DAILY_FREE_LIMIT", "5"))

app = FastAPI(title="AI Video Maker API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for prod
    allow_methods=["*"],
    allow_headers=["*"],
)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# rediss:// URLs already carry TLS info; redis-py handles this automatically
# when the scheme is "rediss" as long as we don't disable cert verification.
r = redis.from_url(REDIS_URL)
celery_app = Celery("video_tasks", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.broker_use_ssl = {"ssl_cert_reqs": "required"}
celery_app.conf.redis_backend_use_ssl = {"ssl_cert_reqs": "required"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class GenerateRequest(BaseModel):
    image_key: str
    motion_strength: int = 127
    fps: int = 7
    num_frames: int = 25


class JobStatus(BaseModel):
    job_id: str
    status: str
    result_url: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Rate limiting (free-tier cap per device)
# ---------------------------------------------------------------------------
def check_and_increment_quota(device_id: str):
    key = f"quota:{device_id}:{datetime.utcnow().strftime('%Y-%m-%d')}"
    count = r.incr(key)
    if count == 1:
        r.expire(key, 86400)
    if count > DAILY_FREE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Daily free limit reached ({DAILY_FREE_LIMIT}/day). Try again tomorrow.",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post("/upload")
async def upload_photo(file: UploadFile = File(...), x_device_id: str = Header(...)):
    """Uploads a user photo to Supabase Storage and returns the storage key."""
    ext = file.filename.split(".")[-1].lower()
    if ext not in ("jpg", "jpeg", "png"):
        raise HTTPException(400, "Only JPG/PNG supported")

    key = f"uploads/{x_device_id}/{uuid.uuid4()}.{ext}"
    contents = await file.read()

    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(400, "Image too large (max 10MB)")

    supabase.storage.from_(SUPABASE_BUCKET).upload(
        key, contents, {"content-type": file.content_type}
    )
    return {"image_key": key}


@app.post("/generate", response_model=JobStatus)
async def generate_video(req: GenerateRequest, x_device_id: str = Header(...)):
    """Enqueues a video generation job. Enforces the daily free-tier cap."""
    check_and_increment_quota(x_device_id)

    job_id = str(uuid.uuid4())
    celery_app.send_task(
        "worker.generate_video_task",
        args=[job_id, req.image_key, req.motion_strength, req.fps, req.num_frames],
        task_id=job_id,
    )
    r.hset(f"job:{job_id}", mapping={"status": "queued", "created_at": time.time()})
    return JobStatus(job_id=job_id, status="queued")


@app.get("/status/{job_id}", response_model=JobStatus)
async def get_status(job_id: str):
    data = r.hgetall(f"job:{job_id}")
    if not data:
        raise HTTPException(404, "Job not found")
    data = {k.decode(): v.decode() for k, v in data.items()}
    return JobStatus(
        job_id=job_id,
        status=data.get("status", "unknown"),
        result_url=data.get("result_url"),
        error=data.get("error"),
    )


@app.get("/healthz")
async def health():
    return {"ok": True}

