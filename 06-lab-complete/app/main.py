"""
Production AI Agent — Part 6 Final Project

Chạy:   uvicorn app.main:app --workers 2
Chấm:   python check_production_ready.py
"""
# Cấu hình logging TRƯỚC khi import các module khác của app,
# để mọi log (kể cả lúc import) đều ra JSON.
from app.logging_config import setup_logging

setup_logging()

import logging  # noqa: E402
import time  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import redis  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.auth import verify_api_key  # noqa: E402
from app.config import settings  # noqa: E402
from app.cost_guard import check_budget, estimate_tokens, record_cost  # noqa: E402
from app.rate_limiter import check_rate_limit  # noqa: E402
from app.redis_client import r, redis_healthy  # noqa: E402
from utils.mock_llm import ask as llm_ask  # noqa: E402

logger = logging.getLogger("app")

START_TIME = time.time()
_state = {"ready": False}


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ──────────────────────────────────────────────────────────
# Lifecycle + graceful shutdown (tiêu chí 11)
# ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log_event("startup", environment=settings.environment, version=settings.app_version,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              daily_budget_usd=settings.daily_budget_usd,
              api_key_configured=bool(settings.agent_api_key))
    if not settings.agent_api_key:
        log_event("api_key_missing", logging.WARNING, note="mọi request /ask sẽ bị 401")
    _state["ready"] = True

    yield

    # Tới đây uvicorn ĐÃ nhận SIGTERM, ngừng nhận kết nối mới và chờ request đang chạy xong.
    # Phần này chỉ còn dọn dẹp. Không tự đăng ký signal.signal(SIGTERM) để khỏi đè handler
    # của uvicorn (đè thì process không tự tắt).
    _state["ready"] = False
    log_event("shutdown", uptime_seconds=round(time.time() - START_TIME, 1))
    r.close()


app = FastAPI(title="Production AI Agent", version=settings.app_version, lifespan=lifespan)


# ──────────────────────────────────────────────────────────
# Middleware: log mỗi request thành 1 dòng JSON
# ──────────────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    log_event("request", method=request.method, path=request.url.path,
              status=response.status_code, ms=round((time.perf_counter() - started) * 1000, 1))
    return response


@app.exception_handler(redis.RedisError)
async def redis_error_handler(request: Request, exc: redis.RedisError):
    # Redis chết giữa chừng: trả 503 rõ ràng thay vì 500 + stack trace
    log_event("redis_error", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Storage unavailable, try again later"})


# ──────────────────────────────────────────────────────────
# Health / readiness (tiêu chí 4, 5)
# ──────────────────────────────────────────────────────────
@app.get("/health")
def health():
    """Liveness: process còn sống → luôn 200. KHÔNG phụ thuộc Redis."""
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "version": settings.app_version,
        "environment": settings.environment,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi đã khởi động xong VÀ Redis kết nối được."""
    if not _state["ready"]:
        raise HTTPException(status_code=503, detail="Agent not ready")
    if not redis_healthy():
        raise HTTPException(status_code=503, detail="Redis not available")
    return {"ready": True}


# ──────────────────────────────────────────────────────────
# Agent endpoint (tiêu chí 6, 7, 8)
# ──────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


# `def` (không phải `async def`): llm_ask() là hàm blocking (time.sleep), FastAPI sẽ chạy nó
# trong threadpool nên không chặn event loop — /health vẫn trả lời trong lúc /ask đang chạy.
@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):   # 1. auth → 401
    check_rate_limit(api_key)                                         # 2. rate limit → 429
    check_budget()                                                    # 3. budget → 402

    answer = llm_ask(body.question)                                   # 4. gọi LLM

    input_tokens = estimate_tokens(body.question)
    output_tokens = estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)                   # 5. ghi nhận chi phí
    log_event("llm_call", input_tokens=input_tokens, output_tokens=output_tokens,
              cost_usd=round(cost, 8))

    return {
        "question": body.question,
        "answer": answer,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8)},
    }
