"""
Rate limiting (tiêu chí 7).

Yêu cầu:
  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window)
  • Vượt giới hạn → HTTP 429, nên kèm header Retry-After
  • State lưu trong Redis để mọi worker / instance dùng chung và vẫn còn sau khi
    restart agent (script chấm sẽ restart agent để kiểm tra)

Thuật toán: sliding window log trên Redis sorted set.
  key   = ratelimit:<hash của API key>
  member = id duy nhất của từng request, score = timestamp
"""
import hashlib
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _bucket(api_key: str) -> str:
    # Lưu hash thay vì key thật: ai đọc được Redis cũng không lấy được API key
    return "ratelimit:" + hashlib.sha256(api_key.encode()).hexdigest()[:16]


def check_rate_limit(key: str) -> None:
    limit = settings.rate_limit_per_minute
    bucket = _bucket(key)
    now = time.time()
    member = f"{now}:{uuid.uuid4().hex}"

    # MULTI/EXEC: 4 lệnh chạy liền một khối, request khác không chen vào giữa được
    # (tránh lỗi "đọc rồi mới ghi" đã thấy ở lời giải mẫu cost guard Part 4).
    pipe = r.pipeline(transaction=True)
    pipe.zremrangebyscore(bucket, 0, now - WINDOW_SECONDS)   # bỏ request cũ hơn 60s
    pipe.zadd(bucket, {member: now})                         # ghi request hiện tại
    pipe.zcard(bucket)                                       # đếm (đã gồm request này)
    pipe.expire(bucket, WINDOW_SECONDS)                      # key tự xoá khi user ngừng gọi
    _, _, count, _ = pipe.execute()

    if count > limit:
        r.zrem(bucket, member)  # request bị từ chối không chiếm suất trong cửa sổ
        oldest = r.zrange(bucket, 0, 0, withscores=True)
        retry_after = int(oldest[0][1] + WINDOW_SECONDS - now) + 1 if oldest else WINDOW_SECONDS
        raise HTTPException(
            status_code=429,
            detail={"error": "Rate limit exceeded", "limit": limit,
                    "window_seconds": WINDOW_SECONDS, "retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after),
                     "X-RateLimit-Limit": str(limit),
                     "X-RateLimit-Remaining": "0"},
        )
