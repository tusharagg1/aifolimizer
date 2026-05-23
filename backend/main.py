import asyncio
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import ws
from app.api import skills as skills_api
from app.api import ops as ops_api
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    await init_redis()
    _PREWARM = [
        "AAPL", "MSFT", "NVDA", "XEQT.TO", "VFV.TO",
        "SPY", "QQQ", "AMZN", "GOOG", "AMD",
    ]
    asyncio.create_task(
        asyncio.to_thread(data_router.get_quotes_batch, _PREWARM, 300)
    )
    scheduler.start_scheduler()
    yield
    scheduler.stop_scheduler()
    await wealthsimple.shutdown()
    await close_redis()
    await close_pool()


app = FastAPI(title="aifolimizer API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://*.vercel.app"],
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


@app.get("/health")
async def health():
    return {"status": "ok"}
