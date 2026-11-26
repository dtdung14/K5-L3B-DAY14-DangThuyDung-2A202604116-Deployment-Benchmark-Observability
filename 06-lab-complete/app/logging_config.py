"""
Structured logging (tiêu chí 10): mỗi dòng log của app là 1 JSON object.

Dùng:
    logger.info("request", extra={"fields": {"path": "/ask", "status": 200}})
→   {"ts": "...", "lvl": "INFO", "logger": "app.main", "event": "request", "path": "/ask", "status": 200}
"""
import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # json.dumps tự escape dấu ", xuống dòng... → luôn ra JSON hợp lệ trên 1 dòng
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    # Thay hẳn handler của root (tương đương basicConfig(force=True)), không phụ thuộc
    # việc module nào đã log trước — tránh lỗi đã gặp ở Part 1.
    root.handlers = [handler]
    root.setLevel(level)
