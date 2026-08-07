"""
AI Photo-to-Video App — Backend API
FastAPI service handling uploads, job creation, and status polling.
Free-to-use app: enforces a per-device daily generation cap to control GPU spend.
"""

import os
import uuid
import time
from datetime import datetime, timedelta

import boto3
from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from celery import Celery
import redis

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
S3_BUCKET = os.environ["S3_BUCKET"]
S3_ENDPOINT = os.environ.get("S3_ENDPOINT")  # e.g. Cloudflare R2 endpoint
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER = os.environ.get("CELERY_BROKER", REDIS_URL)
DAILY_FREE_LIMIT = int(os.environ.get("DAILY_FREE_LIMIT", "5"))  # generations/device/day

app = FastAPI(title="AI Video Maker API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for prod
    allow_methods=["*"],
    allow_headers=["*"],
)

s3 = boto3.client("s3", endpoint_url=S3_ENDPOINT)
r = redis.from_url(REDIS_URL)
celery_app = Celery("video_tasks", broker=CELERY_BROKER, backend=REDIS_URL)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class GenerateRequest(BaseModel):
    image_key: str          # S3 key returned from /upload
    motion_strength: int = 127   # SVD motion_bucket_id (higher = more motion)
    fps: int = 7
    num_frames: int = 25


class JobStatus(BaseModel):
    job_id: str
    status: str              # queued | processing | done | failed
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
    """Uploads a user photo to S3 and returns the storage key for /generate."""
    ext = file.filename.split(".")[-1].lower()
    if ext not in ("jpg", "jpeg", "png"):
        raise HTTPException(400, "Only JPG/PNG supported")

    key = f"uploads/{x_device_id}/{uuid.uuid4()}.{ext}"
    contents = await file.read()

    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(400, "Image too large (max 10MB)")

    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=contents, ContentType=file.content_type)
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
