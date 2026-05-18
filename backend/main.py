import asyncio

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import ws
from app.services import wealthsimple
from app.services import data_router

app = FastAPI(title="aifolimizer API", version="1.0.0")

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
    allowed_origins = ["http://localhost:3000", "https://*.vercel.app"]

    is_origin_allowed = (
        origin in allowed_origins or
        any(allowed.replace("*", "").replace("https://", "") in origin for allowed in allowed_origins if "*" in allowed)
    )

    response = JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": str(type(exc).__name__)},
    )

    if is_origin_allowed:
        response.headers["Access-Control-Allow-Origin"] = origin or "*"
        response.headers["Access-Control-Allow-Credentials"] = "true"

    return response

app.include_router(ws.router, prefix="/ws", tags=["wealthsimple"])


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def startup_event():
    _PREWARM = [
        "AAPL", "MSFT", "NVDA", "XEQT.TO", "VFV.TO",
        "SPY", "QQQ", "AMZN", "GOOG", "AMD",
    ]
    asyncio.create_task(
        asyncio.to_thread(data_router.get_quotes_batch, _PREWARM, 300)
    )


@app.on_event("shutdown")
async def shutdown_event():
    await wealthsimple.shutdown()
