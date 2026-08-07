"""
Celery worker — runs on the GPU pod (RunPod Serverless or a dedicated GPU instance).
Pulls jobs off the queue, runs Stable Video Diffusion (SVD-XT) on the uploaded photo,
uploads the resulting video to S3, and updates job status in Redis.
"""

import os
import io
import time

import boto3
import redis
import torch
from celery import Celery
from PIL import Image
from diffusers import StableVideoDiffusionPipeline
from diffusers.utils import export_to_video

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
S3_BUCKET = os.environ["S3_BUCKET"]
S3_ENDPOINT = os.environ.get("S3_ENDPOINT")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
MODEL_ID = "stabilityai/stable-video-diffusion-img2vid-xt"

s3 = boto3.client("s3", endpoint_url=S3_ENDPOINT)
r = redis.from_url(REDIS_URL)
celery_app = Celery("video_tasks", broker=REDIS_URL, backend=REDIS_URL)

# ---------------------------------------------------------------------------
# Load model once per worker process (kept warm across jobs)
# ---------------------------------------------------------------------------
pipe = StableVideoDiffusionPipeline.from_pretrained(
    MODEL_ID,
    torch_dtype=torch.float16,
    variant="fp16",
)
pipe.to("cuda")
pipe.enable_model_cpu_offload()  # reduces VRAM usage; drop this if you have a big GPU


def _update_job(job_id: str, **fields):
    r.hset(f"job:{job_id}", mapping=fields)


@celery_app.task(name="worker.generate_video_task", bind=True, max_retries=1)
def generate_video_task(self, job_id: str, image_key: str, motion_strength: int, fps: int, num_frames: int):
    try:
        _update_job(job_id, status="processing")

        # 1. Download source image
        obj = s3.get_object(Bucket=S3_BUCKET, Key=image_key)
        image = Image.open(io.BytesIO(obj["Body"].read())).convert("RGB")
        image = image.resize((1024, 576))  # SVD-XT's native aspect ratio

        # 2. Run inference
        generator = torch.manual_seed(42)
        frames = pipe(
            image,
            decode_chunk_size=8,
            generator=generator,
            motion_bucket_id=motion_strength,   # 1-255, higher = more camera/subject motion
            noise_aug_strength=0.02,
            num_frames=num_frames,
            fps=fps,
        ).frames[0]

        # 3. Export to mp4
        local_path = f"/tmp/{job_id}.mp4"
        export_to_video(frames, local_path, fps=fps)

        # 4. Upload result
        result_key = f"results/{job_id}.mp4"
        s3.upload_file(local_path, S3_BUCKET, result_key, ExtraArgs={"ContentType": "video/mp4"})
        result_url = f"{S3_ENDPOINT}/{S3_BUCKET}/{result_key}" if S3_ENDPOINT else \
            f"https://{S3_BUCKET}.s3.amazonaws.com/{result_key}"

        _update_job(job_id, status="done", result_url=result_url)
        os.remove(local_path)

    except Exception as e:
        _update_job(job_id, status="failed", error=str(e))
        raise
