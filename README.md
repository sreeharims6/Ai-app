# AI Video Maker

Turn a photo into a short animated video. Flutter app + FastAPI backend +
self-hosted Stable Video Diffusion (SVD-XT) on rented GPUs.

## Structure

```
backend/
  main.py          FastAPI: upload, generate, status endpoints (runs on cheap CPU host)
  worker.py         Celery worker: runs SVD-XT inference (runs on GPU host)
  Dockerfile.api
  Dockerfile.worker
  docker-compose.yml
mobile/
  lib/main.dart
  lib/screens/home_screen.dart
  lib/services/api_service.dart
  pubspec.yaml
```

## Backend setup

1. Create an S3 or Cloudflare R2 bucket for uploads/results.
2. Deploy `api` service (Dockerfile.api) anywhere cheap — Fly.io, Railway,
   a $5 DigitalOcean droplet, or Cloud Run. It's stateless and CPU-only.
3. Deploy `worker` (Dockerfile.worker) on RunPod Serverless
   (https://runpod.io) using a GPU template — A4000/A10 is enough for
   SVD-XT at 1024x576. Point it at the same Redis instance as the API
   (use RunPod's or a managed Redis like Upstash).
4. Set env vars on both: `S3_BUCKET`, `S3_ENDPOINT`, `REDIS_URL`,
   `DAILY_FREE_LIMIT` (caps free generations/device/day since GPU time
   isn't free even though the app is).

Local dev:
```bash
cd backend
docker compose up --build
```

## Mobile setup

```bash
cd mobile
flutter pub get
flutter run
```

Before building for release, change `ApiService.baseUrl` in
`lib/services/api_service.dart` to your deployed backend URL.

Build the APK:
```bash
flutter build apk --release
# output: build/app/outputs/flutter-apk/app-release.apk
```

## Cost notes

- RunPod Serverless charges per-second of GPU time and scales to zero —
  you only pay while a video is actually generating. Rough figure for
  SVD-XT (25 frames, 1024x576) on an A4000: ~15-25s inference → a few
  cents per video. Confirm current pricing at https://runpod.io/pricing
  before launch, since GPU rates change.
- The `DAILY_FREE_LIMIT` in `main.py` is your main cost control lever for
  a free app — raise/lower it based on actual spend once you have traffic
  data.
