from pathlib import Path
import hmac
import ipaddress
import os
import time
from collections import deque

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

RATE_LIMIT_MAX_KEYS = int(os.getenv("THINKTANK_RATE_LIMIT_MAX_KEYS", "10000"))
ACCESS_TOKEN_HEADER = "X-ThinkTank-Token"
UNAUTHENTICATED_PATHS = {"/api/health"}
TRUSTED_PROXIES = [
    ipaddress.ip_network(entry.strip(), strict=False)
    for entry in os.getenv("THINKTANK_TRUSTED_PROXIES", "").split(",")
    if entry.strip()
]

_rate_limit_hits: dict[tuple[str, str, str], deque[float]] = {}
_rate_limit_last_sweep = 0.0

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
    allow_headers=["Content-Type", ACCESS_TOKEN_HEADER, "Authorization"],
)

if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    unauthorized = _check_access_token(request)
    if unauthorized:
        return unauthorized

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


class BodySizeLimitMiddleware:
    """Buffers request bodies and rejects them once the received bytes exceed the cap,
    regardless of whether a Content-Length header was sent (e.g. chunked uploads)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        max_bytes = MAX_REQUEST_BYTES
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = -1
                if declared < 0:
                    await _send_json(send, 400, {"detail": "Invalid Content-Length header."})
                    return
                if declared > max_bytes:
                    await _send_json(send, 413, {"detail": "Request body too large."})
                    return

        chunks: list[bytes] = []
        received = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            received += len(body)
            if received > max_bytes:
                await _send_json(send, 413, {"detail": "Request body too large."})
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break

        buffered = {"type": "http.request", "body": b"".join(chunks), "more_body": False}
        replayed = False

        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return buffered
            return await receive()

        await self.app(scope, replay_receive, send)


async def _send_json(send, status_code: int, content: dict) -> None:
    response = JSONResponse(status_code=status_code, content=content, headers={"Connection": "close"})
    await send(
        {
            "type": "http.response.start",
            "status": response.status_code,
            "headers": response.raw_headers,
        }
    )
    await send({"type": "http.response.body", "body": response.body})


app.add_middleware(BodySizeLimitMiddleware)


def _check_access_token(request: Request) -> JSONResponse | None:
    expected = os.getenv("THINKTANK_ACCESS_TOKEN", "")
    path = request.url.path
    if not expected or request.method == "OPTIONS":
        return None
    if not path.startswith("/api/") or path in UNAUTHENTICATED_PATHS:
        return None

    provided = request.headers.get(ACCESS_TOKEN_HEADER, "")
    if not provided:
        authorization = request.headers.get("authorization", "")
        scheme, _, credentials = authorization.partition(" ")
        if scheme.lower() == "bearer":
            provided = credentials.strip()

    if provided and hmac.compare_digest(provided.encode(), expected.encode()):
        return None
    return JSONResponse(status_code=401, content={"detail": "Missing or invalid access token."})


def _is_trusted_proxy(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in TRUSTED_PROXIES)


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    if not TRUSTED_PROXIES or not _is_trusted_proxy(peer):
        return peer

    forwarded = [
        part.strip()
        for header in request.headers.getlist("x-forwarded-for")
        for part in header.split(",")
        if part.strip()
    ]
    for hop in reversed(forwarded):
        if not _is_trusted_proxy(hop):
            return hop
    return forwarded[0] if forwarded else peer


def _sweep_rate_limits(now: float) -> None:
    global _rate_limit_last_sweep
    max_window = max((window for _, window in RATE_LIMITS.values()), default=60)
    if now - _rate_limit_last_sweep < max_window and len(_rate_limit_hits) < RATE_LIMIT_MAX_KEYS:
        return
    _rate_limit_last_sweep = now

    for key in list(_rate_limit_hits):
        config = RATE_LIMITS.get((key[0], key[1]))
        window = config[1] if config else max_window
        hits = _rate_limit_hits[key]
        if not hits or now - hits[-1] >= window:
            del _rate_limit_hits[key]

    overflow = len(_rate_limit_hits) - RATE_LIMIT_MAX_KEYS + 1
    if overflow > 0:
        oldest = sorted(_rate_limit_hits, key=lambda key: _rate_limit_hits[key][-1])[:overflow]
        for key in oldest:
            del _rate_limit_hits[key]


def _check_rate_limit(request: Request) -> JSONResponse | None:
    limit_config = RATE_LIMITS.get((request.method, request.url.path))
    if limit_config is None:
        return None

    limit, window_seconds = limit_config
    key = (request.method, request.url.path, _client_ip(request))
    now = time.monotonic()
    _sweep_rate_limits(now)
    hits = _rate_limit_hits.get(key)
    if hits is None:
        hits = _rate_limit_hits[key] = deque()

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

    dev_mode = os.getenv("THINKTANK_DEV", "").lower() in {"1", "true", "yes"}
    host = os.getenv("THINKTANK_HOST") or ("0.0.0.0" if dev_mode else "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"} and not os.getenv("THINKTANK_ACCESS_TOKEN"):
        print(
            f"WARNING: ThinkTank is listening on {host} without THINKTANK_ACCESS_TOKEN; "
            "anyone who can reach this port can spend your provider API keys."
        )
    uvicorn.run("main:app", host=host, port=int(os.getenv("PORT", "8010")), reload=dev_mode)
