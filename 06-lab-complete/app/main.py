"""
Production AI Agent — Part 6 Final Project

Chạy:   uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
Kiểm:   python check_production_ready.py
"""
import json
import logging
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import anyio
import redis
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import close_redis, redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging (tiêu chí 10): mỗi dòng log là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logger() -> logging.Logger:
    log = logging.getLogger("agent")
    log.setLevel(settings.log_level)
    log.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log.addHandler(handler)
    log.propagate = False   # không để root logger in thêm bản không phải JSON
    return log


logger = _setup_logger()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ─────────────────────────────────────────────────────────
# Graceful shutdown (tiêu chí 11)
# ─────────────────────────────────────────────────────────
# Khi nhận SIGTERM, uvicorn ngừng nhận kết nối mới và chờ request đang chạy xong,
# sau đó mới chạy phần shutdown của lifespan bên dưới. Không tự đăng ký signal
# handler để khỏi đè mất handler của uvicorn.
SHUTDOWN_DRAIN_SECONDS = 8

_in_flight = 0
_in_flight_lock = threading.Lock()


def _change_in_flight(delta: int) -> None:
    global _in_flight
    with _in_flight_lock:
        _in_flight += delta


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_event("startup", environment=settings.environment,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              daily_budget_usd=settings.daily_budget_usd,
              monthly_budget_usd=settings.monthly_budget_usd,
              api_key_configured=bool(settings.agent_api_key))
    if not settings.agent_api_key:
        log_event("agent_api_key_missing", logging.WARNING,
                  detail="AGENT_API_KEY chưa đặt — /ask sẽ trả 401 cho mọi request")
    yield
    # Shutdown: chờ nốt request còn dở (phòng hờ), rồi đóng kết nối Redis
    log_event("graceful_shutdown_started", in_flight=_in_flight)
    deadline = time.monotonic() + SHUTDOWN_DRAIN_SECONDS
    while _in_flight > 0 and time.monotonic() < deadline:
        await anyio.sleep(0.1)
    close_redis()
    log_event("graceful_shutdown_complete", in_flight=_in_flight)


app = FastAPI(title="Production AI Agent", version="1.0.0", lifespan=lifespan)


# ─────────────────────────────────────────────────────────
# Middleware: log mỗi request + bắt lỗi không lường trước (không lộ stack trace)
# ─────────────────────────────────────────────────────────
@app.middleware("http")
async def request_logger(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    started = time.perf_counter()
    _change_in_flight(1)
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled_error", extra={"fields": {
            "request_id": request_id, "path": request.url.path}})
        response = JSONResponse(status_code=500,
                                content={"detail": "Internal server error", "request_id": request_id})
    finally:
        _change_in_flight(-1)
    ms = round((time.perf_counter() - started) * 1000, 1)
    response.headers["X-Request-ID"] = request_id
    log_event("request", request_id=request_id, method=request.method,
              path=request.url.path, status=response.status_code, ms=ms)
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    # Trả lỗi rõ ràng, không echo lại input và không có traceback
    errors = [{"field": ".".join(p for p in e.get("loc", [])[1:] if isinstance(p, str)) or "body",
               "message": e.get("msg", "invalid")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "Invalid request", "errors": errors})


@app.exception_handler(redis.RedisError)
async def redis_error_handler(request: Request, exc: redis.RedisError):
    log_event("redis_unavailable", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable"},
                        headers={"Retry-After": "5"})


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


def estimate_tokens(text: str) -> int:
    """Ước lượng ~4 ký tự / token (mock LLM không trả usage)."""
    return max(1, len(text) // 4)


@app.get("/")
def root():
    return {"service": "production-ai-agent", "endpoints": ["/health", "/ready", "POST /ask"]}


@app.get("/health")
def health():
    """Liveness: process còn sống là 200, không phụ thuộc Redis."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi Redis kết nối được."""
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"status": "not_ready", "redis": "unreachable"})
    return {"status": "ready", "redis": "ok"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    question = body.question.strip()
    if not question:
        return JSONResponse(status_code=422, content={"detail": "Invalid request", "errors": [
            {"field": "question", "message": "question must not be blank"}]})

    check_rate_limit(api_key)      # 429 khi vượt RATE_LIMIT_PER_MINUTE
    check_budget()                 # 402 khi hết budget

    started = time.perf_counter()
    answer = llm_ask(question)
    llm_ms = round((time.perf_counter() - started) * 1000, 1)

    input_tokens, output_tokens = estimate_tokens(question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    log_event("llm_call", user_id=body.user_id, input_tokens=input_tokens,
              output_tokens=output_tokens, cost_usd=round(cost, 8), llm_ms=llm_ms)

    return {
        "question": question,
        "answer": answer,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8)},
    }
