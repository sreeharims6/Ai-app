"""
Celery worker — runs on the GPU pod (RunPod).
Pulls jobs off the queue, runs Stable Video Diffusion (SVD-XT) on the uploaded photo,
uploads the resulting video to Supabase Storage, and updates job status in Redis (Upstash).
"""

import os
import io

import redis
import torch
from celery import Celery
from PIL import Image
from diffusers import StableVideoDiffusionPipeline
from diffusers.utils import export_to_video
from supabase import create_client, Client

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "ai-video-maker")
REDIS_URL = os.environ["REDIS_URL"]  # rediss://...
MODEL_ID = "stabilityai/stable-video-diffusion-img2vid-xt"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
r = redis.from_url(REDIS_URL)

celery_app = Celery("video_tasks", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.broker_use_ssl = {"ssl_cert_reqs": "required"}
celery_app.conf.redis_backend_use_ssl = {"ssl_cert_reqs": "required"}

# ---------------------------------------------------------------------------
# Load model once per worker process
# ---------------------------------------------------------------------------
pipe = StableVideoDiffusionPipeline.from_pretrained(
    MODEL_ID,
    torch_dtype=torch.float16,
    variant="fp16",
)
pipe.to("cuda")
pipe.enable_model_cpu_offload()


def _update_job(job_id: str, **fields):
    r.hset(f"job:{job_id}", mapping=fields)


def _public_url(key: str) -> str:
    return supabase.storage.from_(SUPABASE_BUCKET).get_public_url(key)


@celery_app.task(name="worker.generate_video_task", bind=True, max_retries=1)
def generate_video_task(self, job_id: str, image_key: str, motion_strength: int, fps: int, num_frames: int):
    try:
        _update_job(job_id, status="processing")

        # 1. Download source image from Supabase Storage
        image_bytes = supabase.storage.from_(SUPABASE_BUCKET).download(image_key)
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        image = image.resize((1024, 576))

        # 2. Run inference
        generator = torch.manual_seed(42)
        frames = pipe(
            image,
            decode_chunk_size=8,
            generator=generator,
            motion_bucket_id=motion_strength,
            noise_aug_strength=0.02,
            num_frames=num_frames,
            fps=fps,
        ).frames[0]

        # 3. Export to mp4
        local_path = f"/tmp/{job_id}.mp4"
        export_to_video(frames, local_path, fps=fps)

        # 4. Upload result to Supabase Storage
        result_key = f"results/{job_id}.mp4"
        with open(local_path, "rb") as f:
            supabase.storage.from_(SUPABASE_BUCKET).upload(
                result_key, f.read(), {"content-type": "video/mp4"}
            )
        result_url = _public_url(result_key)

        _update_job(job_id, status="done", result_url=result_url)
        os.remove(local_path)

    except Exception as e:
        _update_job(job_id, status="failed", error=str(e))
        raise

