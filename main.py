from pathlib import Path
import os
import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from thinktank.debate_engine import run_debate
from thinktank.judgeai import judge_responses
from thinktank.models import DebateRequest, JudgeAIRequest, ProviderValidationRequest
from thinktank.providers import (
    PROVIDERS,
    ProviderConfigurationError,
    configured_providers,
    public_provider_metadata,
    validate_provider_key,
)


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
FRONTEND_DIST = BASE_DIR / "dist"
DEFAULT_ALLOWED_ORIGINS = "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8010,http://localhost:8010"
MAX_REQUEST_BYTES = int(os.getenv("THINKTANK_MAX_REQUEST_BYTES", str(64 * 1024)))
RATE_LIMITS = {
    ("POST", "/api/validate-provider"): (
        int(os.getenv("THINKTANK_VALIDATE_RATE_LIMIT", "20")),
        int(os.getenv("THINKTANK_VALIDATE_RATE_WINDOW_SECONDS", "60")),
    ),
    ("POST", "/api/debate"): (
        int(os.getenv("THINKTANK_DEBATE_RATE_LIMIT", "6")),
        int(os.getenv("THINKTANK_DEBATE_RATE_WINDOW_SECONDS", "60")),
    ),
    ("POST", "/api/judgeai"): (
        int(os.getenv("THINKTANK_JUDGEAI_RATE_LIMIT", "10")),
        int(os.getenv("THINKTANK_JUDGEAI_RATE_WINDOW_SECONDS", "60")),
    ),
}

_rate_limit_hits: dict[tuple[str, str, str], deque[float]] = defaultdict(deque)

app = FastAPI(title="ThinkTank Pixel Council", version="2.0.0")

allowed_origins = [
    origin.strip()
    for origin in os.getenv("THINKTANK_ALLOWED_ORIGINS", DEFAULT_ALLOWED_ORIGINS).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                return JSONResponse(status_code=413, content={"detail": "Request body too large."})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header."})

    limited = _check_rate_limit(request)
    if limited:
        return limited

    response: Response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; connect-src 'self' https:; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'",
    )
    return response


def _check_rate_limit(request: Request) -> JSONResponse | None:
    limit_config = RATE_LIMITS.get((request.method, request.url.path))
    if limit_config is None:
        return None

    limit, window_seconds = limit_config
    client_host = request.client.host if request.client else "unknown"
    key = (request.method, request.url.path, client_host)
    now = time.monotonic()
    hits = _rate_limit_hits[key]

    while hits and now - hits[0] >= window_seconds:
        hits.popleft()

    if len(hits) >= limit:
        retry_after = max(1, int(window_seconds - (now - hits[0])))
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Try again shortly."},
            headers={"Retry-After": str(retry_after)},
        )

    hits.append(now)
    return None


def _ensure_supported_model(provider: str, model: str) -> None:
    provider_info = PROVIDERS.get(provider)
    if provider_info is None or model not in provider_info.models:
        raise HTTPException(status_code=400, detail="Unsupported provider/model combination.")


def _ensure_configured_model(provider: str, model: str) -> None:
    _ensure_supported_model(provider, model)
    if provider not in configured_providers():
        raise HTTPException(status_code=503, detail=f"{provider} is not configured on this server.")


def _safe_dist_file(path: str) -> Path | None:
    try:
        base = FRONTEND_DIST.resolve()
        requested = (FRONTEND_DIST / path).resolve()
        requested.relative_to(base)
    except (OSError, ValueError):
        return None

    if requested.is_file():
        return requested
    return None


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/providers")
async def providers() -> dict:
    return {"providers": public_provider_metadata(configured_providers())}


@app.post("/api/validate-provider")
async def validate_provider(payload: ProviderValidationRequest) -> dict:
    _ensure_configured_model(payload.provider, payload.model)
    return await validate_provider_key(payload)


@app.post("/api/debate")
async def debate(payload: DebateRequest) -> StreamingResponse:
    for agent in payload.agents:
        _ensure_configured_model(agent.provider, agent.model)

    return StreamingResponse(
        run_debate(payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/judgeai")
async def judgeai(payload: JudgeAIRequest) -> dict:
    _ensure_configured_model("openai", payload.model)
    try:
        return await judge_responses(payload)
    except ProviderConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/{path:path}")
async def serve_frontend(path: str):
    index_file = FRONTEND_DIST / "index.html"
    requested = _safe_dist_file(path)

    if requested is not None:
        return FileResponse(requested)

    if index_file.exists():
        return FileResponse(index_file)

    return JSONResponse(
        status_code=404,
        content={
            "detail": "Frontend build not found. Run `npm run build` from the project root.",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8010")), reload=True)
