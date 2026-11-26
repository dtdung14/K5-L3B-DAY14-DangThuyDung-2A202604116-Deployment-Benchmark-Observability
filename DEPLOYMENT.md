# Deployment Information

## Public URL

https://agent-production-09013268.up.railway.app

## Platform

Railway. Deploy thư mục `06-lab-complete/` bằng Railway CLI (`railway up`), build từ `Dockerfile` theo `06-lab-complete/railway.toml`.

Project gồm 2 service:

| Service | Nguồn | Vai trò |
|---|---|---|
| `agent` | `06-lab-complete/Dockerfile` | FastAPI agent (Part 6), domain public ở trên |
| `Redis` | Redis database của Railway | Lưu state rate limit và chi phí; agent gọi qua mạng nội bộ `redis.railway.internal:6379` |

## Test Commands

Đặt biến trước khi chạy (key lấy trong Railway → service `agent` → Variables, không ghi vào repo):

```bash
BASE=https://agent-production-09013268.up.railway.app
AGENT_API_KEY=<key trong Railway Variables>
```

### Tự kiểm tra CP6 (chỉ in mã HTTP, không lộ key)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả chạy ngày 2026-11-26:

```text
health: 200
ready:  200
ask không key: 401
ask có key:    200
```

### Health check

```bash
curl https://agent-production-09013268.up.railway.app/health
```

```json
{"status":"ok","uptime_seconds":23.7,"version":"1.0.0","environment":"production","timestamp":"2026-11-26T09:25:58.199494+00:00"}
```

### API test (có API key)

```bash
curl -X POST https://agent-production-09013268.up.railway.app/ask \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is deployment?"}'
```

```json
{"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":4,"output_tokens":19,"cost_usd":1.2e-05}}
```

### Rate limit (10 request/phút)

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -w '%{http_code} ' -X POST "$BASE/ask" \
    -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"hi"}'
done
```

```text
200 200 200 200 200 200 200 200 429 429 429 429
```

(Trước vòng lặp đã có 2 request 200 ở bước kiểm tra trên, nên 8 + 2 = 10 thì bắt đầu 429.)

### Script chấm với URL public

```bash
cd 06-lab-complete
python check_production_ready.py --public-url https://agent-production-09013268.up.railway.app
```

```text
✅ Public URL: GET https://agent-production-09013268.up.railway.app/health → 200
```

## Environment Variables Set

Đặt trên Railway (service `agent` → Variables), **không** ghi trong code hay trong repo:

| Biến | Giá trị | Ghi chú |
|---|---|---|
| `AGENT_API_KEY` | (secret, 43 ký tự ngẫu nhiên) | Tạo bằng `secrets.token_urlsafe(32)`, đưa vào qua `railway variable set --stdin` |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Biến tham chiếu: Railway tự điền URL nội bộ của service Redis |
| `ENVIRONMENT` | `production` | App dừng ngay nếu thiếu `AGENT_API_KEY` khi ở production |
| `RATE_LIMIT_PER_MINUTE` | `10` | |
| `DAILY_BUDGET_USD` | `1.0` | |
| `PORT` | do Railway tự cấp (`8080`) | `CMD` trong Dockerfile đọc `${PORT:-8000}` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
