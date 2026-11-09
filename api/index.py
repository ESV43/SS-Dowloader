from __future__ import annotations

import asyncio
import logging
import sys
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

# Ensure /var/task (Vercel) and local api dir are importable
_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE), str(_HERE.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from _scraper import (
        BlockedError,
        DeckUnavailableError,
        InvalidUrlError,
        ScraperError,
        fetch_deck,
        normalize_url,
    )
    from _builder import BuildError, build_deck
except Exception:
    from api._scraper import (  # type: ignore
        BlockedError,
        DeckUnavailableError,
        InvalidUrlError,
        ScraperError,
        fetch_deck,
        normalize_url,
    )
    from api._builder import BuildError, build_deck  # type: ignore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("slideswift")

app = FastAPI(title="SlideSwift API", docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def all_errors(request: Request, exc: Exception):
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Something went wrong. Please try again."})


@app.get("/api/health")
async def health():
    return {"status": "ok", "runtime": "vercel"}


@app.post("/api/analyze")
async def analyze(payload: dict):
    url = (payload or {}).get("url", "")
    try:
        normalize_url(url)
        deck = await fetch_deck(url)
        return deck.public()
    except InvalidUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except DeckUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except BlockedError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except ScraperError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        logger.exception("Analyze failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not read that SlideShare presentation right now.")


@app.post("/api/build")
async def build(payload: dict):
    url = (payload or {}).get("url", "")
    fmt = str((payload or {}).get("format", "pdf")).lower().strip()
    if fmt not in {"pdf", "pptx", "zip"}:
        raise HTTPException(status_code=400, detail="Format must be pdf, pptx, or zip.")
    try:
        normalize_url(url)
        deck = await fetch_deck(url)
        result = await asyncio.to_thread(build_deck, deck, fmt)
        return FileResponse(
            path=str(result.path),
            media_type=result.media_type,
            filename=result.filename,
            headers={
                "Content-Disposition": f'attachment; filename="{result.filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except InvalidUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except DeckUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except BlockedError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except (ScraperError, BuildError) as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Build failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Build failed: {type(exc).__name__}: {exc}")
