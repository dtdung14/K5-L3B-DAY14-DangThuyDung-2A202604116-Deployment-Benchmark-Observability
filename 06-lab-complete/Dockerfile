# ============================================================
# Production Dockerfile — multi-stage (tiêu chí 1)
# Build:  docker build -t day14-agent .        (chạy trong 06-lab-complete/)
# Run:    docker run -p 8000:8000 -e AGENT_API_KEY=... day14-agent
# ============================================================

# ── Stage 1: builder — cài dependencies vào một virtualenv ──
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Chỉ copy requirements trước → layer cài thư viện được cache khi chỉ sửa code
COPY requirements.txt .
RUN pip install -r requirements.txt


# ── Stage 2: runtime — chỉ có Python slim + venv + code ──
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

# User không có quyền root, không có shell đăng nhập
RUN groupadd --system app && useradd --system --gid app --no-create-home app

WORKDIR /app

# Venv phải nằm cùng đường dẫn /opt/venv ở cả 2 stage (script trong venv ghi cứng đường dẫn)
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app/ ./app/
COPY --chown=app:app utils/ ./utils/

USER app

EXPOSE 8000

# Docker tự gọi /health; dùng Python có sẵn (image slim không có curl)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.getenv('PORT', '8000'), timeout=3)" || exit 1

# sh -c để thay ${PORT} (Railway/Render cấp); `exec` để uvicorn THAY THẾ sh và thành PID 1
# → nhận trực tiếp SIGTERM khi `docker stop`. Graceful timeout 8s < 10s của docker stop.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --timeout-graceful-shutdown 8"]
