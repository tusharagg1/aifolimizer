import asyncio
import logging
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import ws
from app.api import skills as skills_api
from app.api import ops as ops_api
from app.api import agents as agents_api
from app.services import wealthsimple
from app.services import data_router
from app.jobs import scheduler
from app.security import configure_logging
from app.db import init_pool, close_pool
from app.cache import init_redis, close_redis
from app.core.config import settings
from app.core.sentry import init_sentry

configure_logging()
init_sentry(settings)

_ALLOWED_ORIGIN_EXACT = {"http://localhost:3000"}
_ALLOWED_ORIGIN_PATTERN = re.compile(r"^https://[a-zA-Z0-9-]+\.vercel\.app$")


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return False
    if origin in _ALLOWED_ORIGIN_EXACT:
        return True
    return bool(_ALLOWED_ORIGIN_PATTERN.match(origin))


def _fire_mfa_notify() -> None:
    """Spawn mfa_notify.py in the background. Cooldown lives inside the
    script (6h) so calling this on every restore failure is safe."""
    script = Path(__file__).parent / "scripts" / "mfa_notify.py"
    if not script.exists():
        return
    try:
        subprocess.Popen(
            [sys.executable, str(script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(Path(__file__).parent),
        )
    except Exception:
        logging.getLogger(__name__).debug("suppressed exception", exc_info=True)


def _clear_mfa_flag() -> None:
    """Session restored OK — reset the MFA notify cursor so the next real
    expiry fires a fresh heads-up instead of being suppressed by the window."""
    try:
        (Path.home() / ".aifolimizer" / ".mfa-notify.last").unlink(missing_ok=True)
    except OSError:
        # Cursor file absent or unwritable — nothing to reset.
        pass


def _fire_system_up() -> None:
    """Push one 'backend online' heads-up per boot, deduped 30 min so a
    restart/crash-loop doesn't spam. Non-fatal."""
    import time

    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return
    marker = Path.home() / ".aifolimizer" / ".online-notify.last"
    try:
        if marker.exists() and time.time() - float(marker.read_text(encoding="utf-8").strip() or 0) < 1800:
            return
    except (OSError, ValueError):
        # Unreadable/garbage marker — treat as no prior notify and continue.
        pass
    try:
        import httpx

        httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={
                "chat_id": settings.telegram_chat_id,
                "text": "🟢 aifolimizer online — backend up, scheduler + worker running.",
            },
            timeout=10.0,
        ).raise_for_status()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        logging.getLogger(__name__).debug("suppressed exception", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    await init_redis()
    _PREWARM = [
        "AAPL",
        "MSFT",
        "NVDA",
        "XEQT.TO",
        "VFV.TO",
        "SPY",
        "QQQ",
        "AMZN",
        "GOOG",
        "AMD",
    ]
    asyncio.create_task(asyncio.to_thread(data_router.get_quotes_batch, _PREWARM, 300))
    # Re-seed the WS session from disk so the scheduler keeps the token warm
    # across restarts (its ticks refresh it) instead of idling until a manual
    # login. Failure is non-fatal — falls back to lazy login on first use.
    # If restore returns None (no file / stale / WS rejected), fire a single
    # Telegram heads-up via mfa_notify.py. Event-driven: no polling watchdog
    # required — user gets one message per real expiry event.
    # System-up heads-up: one Telegram ping per boot (deduped) so the user
    # knows the machine came back after a shutdown/restart.
    asyncio.create_task(asyncio.to_thread(_fire_system_up))
    try:
        sid = await asyncio.to_thread(wealthsimple.restore_session)
        if sid is None:
            asyncio.create_task(asyncio.to_thread(_fire_mfa_notify))
        else:
            _clear_mfa_flag()
    except Exception:
        logging.getLogger(__name__).debug("suppressed exception", exc_info=True)
    scheduler.start_scheduler()
    yield
    scheduler.stop_scheduler()
    await wealthsimple.shutdown()
    await close_redis()
    await close_pool()


app = FastAPI(title="aifolimizer API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # FastAPI's CORSMiddleware does NOT expand `*` in allow_origins —
    # the previous `https://*.vercel.app` entry was a dead literal that
    # never matched. The frontend was removed; the only legitimate browser
    # caller is a local dev server. Vercel-preview support, if needed
    # again, must use `allow_origin_regex=...` instead.
    allow_origins=["http://localhost:3000"],
    allow_origin_regex=r"^https://[a-zA-Z0-9-]+\.vercel\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    origin = request.headers.get("origin")
    response = JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": str(type(exc).__name__)},
    )
    if _origin_allowed(origin):
        response.headers["Access-Control-Allow-Origin"] = origin  # type: ignore[assignment]
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


app.include_router(ws.router, prefix="/ws", tags=["wealthsimple"])
app.include_router(skills_api.router, prefix="/skills", tags=["skills"])
app.include_router(ops_api.router, prefix="/ops", tags=["ops"])
app.include_router(agents_api.router, prefix="/agents", tags=["agents"])


@app.get("/health")
async def health():
    return {"status": "ok"}
