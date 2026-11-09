# SlideSwift (Vercel)

A fast SlideShare downloader web app — **Vercel-ready**. Paste a public SlideShare link and download:

- **PDF** — optimized (1440px, JPEG q78, baseline) for a crisp, lean file
- **PPTX** — 16:9 widescreen PowerPoint, one high-resolution slide per page
- **ZIP** — every slide image at original quality

## How it works (Vercel/serverless)

- The frontend is static (`/static`, `index.html`) served by Vercel.
- A Python FastAPI serverless function at `/api/index.py` handles `/api/*` (rewritten via `vercel.json`).
- Slide data is read from SlideShare’s embedded `__NEXT_DATA__` payload using a browser-like HTTP client (`curl_cffi` impersonation, with `httpx` fallback) — **no Playwright/Chromium required**.
- Slide images download in parallel; PDFs/PPTXs are built on demand and returned as a file download.

> **Note:** SlideShare may occasionally serve an anti-bot challenge from some server IP ranges. The scraper retries and reports a clear error if blocked. For maximum reliability on very large decks or restrictive networks, a container host (Fly.io/Docker) with the browser-enabled build is the fallback option.

## Deploy to Vercel

1. Push this repo to GitHub.
2. In Vercel: **Add New Project** → import the repo.
3. Framework preset: **Other** (or leave auto-detected). Vercel will pick up `api/*.py` (Python serverless) and serve static files.
4. Deploy. (No build command required.)

CLI:
```bash
npm i -g vercel
vercel        # preview
vercel --prod # production
```

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`  | `/api/health`  | Health check |
| `POST` | `/api/analyze` | `{ "url": "..." }` → deck metadata (title, author, slides, thumbnails) |
| `POST` | `/api/build`   | `{ "url": "...", "format": "pdf|pptx|zip" }` → file download |

## Local dev

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn api.index:app --reload --port 8000
# open http://localhost:8000  (serve static separately, or use vercel dev)
```

Or with Vercel CLI: `vercel dev`.

## Limits & notes

- Vercel serverless functions have execution time and memory limits (Hobby: ~10s/1024MB; Pro higher). Very large decks (100+ slides) may time out on Hobby — PDF/PPTX encoding is CPU-heavy. Pro plan or a container host is recommended for large decks.
- Only public presentations are supported.
- SlideSwift is not affiliated with SlideShare or Scribd. Download only presentations you have permission to use.
