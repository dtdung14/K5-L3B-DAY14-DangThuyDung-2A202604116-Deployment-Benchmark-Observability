# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ report JSON trong `reports/` (copy từ `06-lab-complete/benchmark-observability/develop/reports/`), CSV trong `benchmark-results/` và screenshot thật.

**Họ tên:** Đặng Thùy Dung
**Ngày chạy:** 2026-11-26
**Môi trường:** Laptop Intel Core Ultra 5 226V, Windows + WSL2 Ubuntu 24.04 (8 CPU, 7.4 GB RAM), Python 3.12, Docker Desktop (8 CPU / ~8 GB RAM)

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Ở tải bình thường (concurrency 1–50) p95 đo được 219–242 ms, ngưỡng 500 ms chừa gấp đôi để chịu jitter và tải tăng. Đặt theo p95 chứ không theo average vì average che mất tail latency (mục 2.2). 500 ms vẫn đủ nhanh cho người dùng chat. |
| Error rate `/ask` | < 2 % | Mock LLM tự sinh khoảng 1 % lỗi upstream (`MOCK_ERROR_RATE=0.01`), nên SLO phải cao hơn mức nền đó; 2 % = error budget 2 lỗi / 100 request. Ngưỡng alert (5 % trong 1 phút) lỏng hơn SLO để tránh báo động vì spike ngắn. |
| Accuracy trên golden dataset | ≥ 90 % | Bản v2 ban đầu đạt 91.3 %, sau khi sửa KB đạt 100 %. Đặt 90 % để chặn mọi phiên bản tệ hơn đáng kể; kèm điều kiện **không có câu nào regression** so với baseline (quality gate của `run_eval.py`). |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.22 | 187.5 | 219.2 | 1705.2 | 0.33 % | ✅ PASS |
| 10 | 34.46 | 189.1 | 220.4 | 1709.9 | 0.00 % | ✅ PASS |
| 50 | 118.58 | 192.5 | 241.6 | 1689.3 | 2.33 % | ❌ FAIL (error rate) |
| 100 | 142.72 | 229.7 | 573.5 | 1732.0 | 0.67 % | ❌ FAIL (p95) |

Lệnh: `python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json`. Mỗi mức 300 request.

**Nhận xét:** RPS có tăng tuyến tính theo concurrency không? Vì sao?

- **Từ 1 lên 10: gần tuyến tính** (4.22 → 34.46 req/s, gấp 8.2 lần với concurrency gấp 10). Mỗi request chủ yếu là *chờ* mock LLM (`asyncio.sleep` ~150–250 ms), không tốn CPU, nên một event loop phục vụ được nhiều request chờ cùng lúc; p50 gần như không đổi (187.5 → 189.1 ms). Theo định luật Little, RPS ≈ concurrency / latency = 10 / 0.25 s ≈ 40 req/s, khớp với số đo.
- **Từ 50 lên 100: không còn tuyến tính.** Lý tưởng c=50 là ~220 req/s và c=100 là ~400 req/s, thực tế chỉ 118.58 và 142.72. Hai nguyên nhân:
  1. **Agent bắt đầu bão hòa:** chỉ có 1 process uvicorn (1 event loop, 1 core). Phần CPU của mỗi request (parse JSON, ghi log JSON, cập nhật metrics) cộng dồn, request phải xếp hàng chờ event loop. Thấy rõ ở c=100: p50 tăng 189 → 230 ms, p95 tăng vọt 220 → 573.5 ms và vi phạm SLO.
  2. **Bài test quá ngắn so với tail latency:** 300 request ở c=100 chỉ chạy 2.1 giây, trong khi mỗi request chậm bất thường mất ~1.7 giây. Vài request chậm đến cuối kéo dài cả bài test, làm RPS đo được thấp hơn năng lực thật. Muốn đo năng lực tối đa nên chạy theo thời gian (`--duration 30`).
- **Error rate 2.33 % ở c=50 không do tải:** 7/300 request lỗi `503` là lỗi upstream ngẫu nhiên của mock (`MOCK_ERROR_RATE=0.01`), không phải agent quá tải. c=100 tải cao hơn mà chỉ có 0.67 %. Với 300 request, 1 % lỗi ngẫu nhiên có thể dao động từ 0 tới ~7 lỗi; cần mẫu lớn hơn để kết luận về error rate.
- **Kết luận:** một instance v2 giữ được SLO p95 tới khoảng concurrency 50; vượt mức đó cần scale ngang (thêm worker hoặc instance, xem mục 5).

### 2.2 Tail latency

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 188.6 | 217.2 | 233.6 |
| 0.03 (mặc định) | 218.1 | 218.0 | 1695.2 |
| 0.1 | 325.6 | 1691.9 | 1709.6 |

Mỗi dòng: khởi động lại v2 với `MOCK_SLOW_RATE=<x>`, chạy `--requests 300 --concurrency 20`, file `reports/load_v2_slow_<x>.json`. p50 gần như không đổi: 188.4 / 186.6 / 190.2 ms.

**Nhận xét:** Chỉ số nào nhạy nhất với request chậm? Vì sao SLO không nên đặt theo average?

- **p99 nhạy nhất ở mức chậm thấp:** chỉ 3 % request chậm (`0 → 0.03`) đã đẩy p99 từ 233.6 lên 1695.2 ms (gấp 7 lần), trong khi p95 gần như không đổi (217.2 → 218.0) và avg chỉ tăng 16 % (188.6 → 218.1).
- **p95 nhảy khi tỉ lệ chậm vượt 5 %:** ở 10 % request chậm, p95 lên 1691.9 ms (vi phạm SLO 500 ms). Lý do: p95 là ngưỡng mà 95 % request nhanh hơn; khi hơn 5 % request chậm thì chính p95 rơi vào nhóm chậm.
- **avg luôn "trông ổn":** ngay cả khi 10 % người dùng phải chờ ~1.7 giây, avg chỉ là 325.6 ms, vẫn dưới SLO 500 ms. Nếu đặt SLO theo average, ta sẽ không bao giờ phát hiện 1/10 người dùng đang có trải nghiệm tệ. Average bị kéo về phía đa số request nhanh, còn percentile cho biết trực tiếp "x % người dùng chờ lâu hơn bao nhiêu".
- Request chậm cũng làm giảm throughput khi concurrency cố định: 102.54 → 64.51 → 48.77 req/s, vì một slot concurrency bị giữ ~1.7 giây cho mỗi request chậm.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3 % (1/23) | 91.3 % (21/23) | 100 % (23/23) |
| Avg tokens / câu | 56.8 | 109.0 | 117.7 |
| Tổng cost (USD) | 0.000400 | 0.000760 | 0.000825 |
| Cost / câu đúng (USD) | 0.00039975 | 0.00003619 | 0.00003586 |
| Latency p50 / p95 (ms) | 155.3 / 191.5 | 184.7 / 204.1 | 183.9 / 213.6 |
| Quality gate | ❌ FAIL (ngưỡng 80 %) | ✅ PASS (ngưỡng 80 %, 0 regression so với v1) | ✅ PASS (ngưỡng 100 %, 0 regression so với v2) |

Lệnh:

```bash
python eval/run_eval.py --url http://localhost:8001 --out reports/eval_v1.json
python eval/run_eval.py --url http://localhost:8002 --out reports/eval_v2.json --baseline reports/eval_v1.json
# sửa KNOWLEDGE_BASE, tắt rồi bật lại v2
python eval/run_eval.py --url http://localhost:8002 --min-accuracy 1.0 --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json
```

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` "JWT khác gì API key trong xác thực?" | `token`, `hết hạn`, `chữ ký` (thiếu cả 3, score 0) | Câu hỏi không khớp từ khoá nào trong `KNOWLEDGE_BASE` nên agent trả câu chung chung. Thêm entry trigger `["jwt"]`, nội dung giải thích JWT là **token** có **chữ ký** của server, có thời điểm **hết hạn** (`exp`), khác API key là chuỗi cố định không tự hết hạn. |
| `scaling-03` "Kubernetes HPA scale dựa trên những chỉ số nào?" | `cpu`, `memory`, `custom metrics` (thiếu cả 3, score 0) | Tương tự, không có tài liệu nào về Kubernetes. Thêm entry trigger `["kubernetes", "hpa", "autoscal"]`, nội dung: HPA tăng giảm số pod theo **CPU**, **memory** hoặc **custom metrics**. |

Trigger chọn hẹp (`jwt`, `kubernetes`/`hpa`): đã kiểm tra chỉ đúng 2 câu này trong golden dataset chứa các từ đó, nên không làm đổi câu trả lời của câu khác. Kết quả: 2 câu được cải thiện, **0 regression**. Entry được sửa trong `develop/utils/mock_llm.py` và chép sang `production/utils/mock_llm.py` để stack Docker cũng đạt 100 %. Không sửa `golden_dataset.jsonl` (đáp án).

**Quality gate trong CI (Exercise 7.3):** `run_eval.py` trả **exit code 1** khi accuracy dưới `--min-accuracy` hoặc có câu regression so với `--baseline`, và **0** khi đạt. Chạy thử eval v1 với `--baseline reports/eval_v2.json` thì thấy 20 câu regression, accuracy 4.3 %, `Quality gate ❌ FAIL`, **exit code = 1**. CI chỉ cần chạy lệnh này như một step: exit code khác 0 làm step fail, job fail, và nếu job được đặt là *required status check* trong branch protection thì GitHub không cho merge PR.

```yaml
# .github/workflows/eval.yml
name: quality-gate
on: [pull_request]
jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - name: Run agent v2 + eval (chặn merge nếu tệ hơn baseline)
        working-directory: 06-lab-complete/benchmark-observability/develop
        run: |
          pip install -r requirements.txt
          AGENT_VERSION=v2 MOCK_ERROR_RATE=0 PORT=8000 python app.py &
          sleep 3
          python eval/run_eval.py --url http://localhost:8000 \
            --min-accuracy 0.9 --baseline reports/eval_v2_fixed.json
```

`MOCK_ERROR_RATE=0` để lỗi upstream ngẫu nhiên không làm CI fail oan; baseline là report đã commit của phiên bản đang chạy production.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- **Chất lượng:** v2 (RAG) tăng accuracy từ 4.3 % lên 91.3 % (100 % sau khi bổ sung tài liệu). v1 chỉ đúng 1 câu nhờ câu trả lời có sẵn về deploy.
- **Chi phí mỗi câu tăng gần gấp đôi:** tokens/câu 56.8 → 109.0, tổng cost 0.000400 → 0.000760 USD, vì prompt v2 chèn thêm tài liệu tìm được.
- **Nhưng chi phí cho mỗi câu đúng giảm ~11 lần:** 0.00039975 → 0.00003619 USD. v1 rẻ mỗi câu nhưng gần như toàn trả lời sai, tiền đó bỏ đi. Vì vậy "cost / câu đúng" là chỉ số đúng để so hai phiên bản: nó tính cả chi phí lẫn chất lượng.
- **Latency tăng nhẹ:** p50 155.3 → 184.7 ms (+29 ms) do thêm bước retrieval (~20 ms) và prompt dài hơn; p95 vẫn chỉ 204.1 ms, dưới SLO 500 ms rất xa.
- **Bổ sung KB tốn thêm ít:** bản sửa thêm 8.7 tokens/câu và +9.5 ms p95 nhưng đúng thêm 2 câu, nên cost / câu đúng còn giảm tiếp (0.00003619 → 0.00003586).
- **Kết luận:** v2 đáng triển khai: đổi ~30 ms latency và gấp đôi tiền mỗi câu để có câu trả lời đúng gấp ~20 lần.

---

## 4. Observability stack (production)

Stack chạy bằng `docker compose -p k5-day14-observability up -d --build`; tải tạo bằng Locust headless 30 user (`-u 30 -r 5`) chạy nền suốt các thí nghiệm. Unit test alert rules: `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

**Hai lỗi gặp phải khi chạy và cách xử lý:**

- `production/Dockerfile` mẫu không chạy được (`ModuleNotFoundError: No module named 'uvicorn'`): user `agent` có home là `/app` nên Python tìm package `--user` ở `/app/.local`, trong khi package được copy vào `/home/agent/.local`. Sửa bằng `ENV PYTHONUSERBASE=/home/agent/.local`.
- `docker compose run locust ...` (lệnh trong CHECKPOINTS) **tự đưa số agent về 2** vì compose bật lại các service phụ thuộc (nginx → agent) theo `deploy.replicas: 2`. Kiểm tra bằng `docker events`: agent-2 được tạo đúng lúc Locust "1 instance" bắt đầu, agent-3 bị xoá ngay trước Locust "3 instance". Hai lần đo đầu bị bỏ; số liệu ở mục 5 chạy lại với `docker compose run --no-deps ...`, có kiểm tra số container agent trước và sau mỗi lần.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): [screenshots/grafana-dashboard.png](screenshots/grafana-dashboard.png)
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian): [screenshots/jaeger-slow-trace.png](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger: [screenshots/jaeger-error-trace.png](screenshots/jaeger-error-trace.png). Span `chat mock-llm-small` và `agent.run` có dấu lỗi đỏ, `POST /ask` trả 503. Event `exception` (`exception.message = "Upstream LLM error: 503 Service Unavailable (mock)"`) nằm trên 2 span đó (đọc qua Jaeger API `/api/v3/traces/8c80383129abf9a6d70a322fcd129485`); ảnh chụp ở chế độ thu gọn nên chưa mở chi tiết event.
- [x] Trang Alerts của Prometheus với 2 alert đang **firing** (`AgentHighErrorRate`, `AgentHighLatencyP95`): [screenshots/prometheus-alert-firing.png](screenshots/prometheus-alert-firing.png)

**Trả lời Exercise 7.4** (stack 3 instance, 100 user Locust, v2):

1. **p95 và avg:** panel Latency p95 lúc chụp là 292.67 ms; theo CSV Locust 3 instance: avg 227.3 ms, p95 270 ms, p99 1700 ms. Avg và p95 gần nhau, nhưng p99 cao gấp ~7 lần: đa số request nhanh (~200 ms), còn khoảng 3 % request bị LLM chậm thêm ~1.5 s (`MOCK_SLOW_RATE=0.03`). Khoảng cách giữa avg và p99 chính là tail latency mà average che mất.
2. **Requests/s theo instance:** Nginx chia đều theo round-robin. Gửi 9 request thử: mỗi instance nhận 3 (`172.21.0.4`, `.8`, `.9`); trên panel 3 đường gần như trùng nhau.
3. **Input tokens/s > output tokens/s** (1118.5 so với 593.9 token/s): agent v2 đưa vào prompt cả system prompt, tài liệu tìm được (retrieval) và câu hỏi, còn output chỉ là câu trả lời ngắn. RAG làm phần input dài ra.
4. **`curl http://localhost:8080/metrics` → 404:** Nginx chặn `/metrics` từ bên ngoài. Prometheus scrape thẳng từng agent trong mạng Docker nội bộ, không đi qua Nginx; mở `/metrics` ra Internet vừa lộ thông tin nội bộ (version, model, số liệu chi phí), vừa cho người ngoài gọi endpoint tốn CPU.

### 4.2 Thí nghiệm "phá hệ thống"

Mỗi thí nghiệm: sửa `.env` rồi `docker compose -p k5-day14-observability up -d --no-deps --scale agent=3 agent`; thời gian đo từ lúc lệnh này chạy xong, poll `GET /api/v1/alerts` mỗi 3 giây.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` | pending 28 s → **firing 88 s** (`for: 1m`) | Error rate (19.0 %), LLM calls theo kết quả (đường `error` tăng) |
| B | `MOCK_SLOW_RATE=0.3` (chạy chồng lên A) | `AgentHighLatencyP95` | pending 24 s → **firing 145 s** (`for: 2m`) | Latency p50/p95/p99: p95 lên 1891 ms, p50 gần như không đổi |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` | pending 27 s → **firing 145 s** (`for: 2m`) | Cost burn rate: $35.4/giờ, gấp ~14 lần mức nền $2.45/giờ; LLM latency p95 0.495 s |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` | pending 12 s → **firing 43 s** (`for: 30s`) | Instances up 3 → 2. Panel Error rate vẫn **0 %** dù Nginx ghi 26 lần `upstream timed out` (người dùng nhận 504) |
| E | `AGENT_VERSION=v1` | **Không có** (theo dõi 150 s) | — | Tokens/s giảm (input 1118.5 → 680.1, output 593.9 → 371.7), bảng "Phiên bản đang chạy" đổi sang v1, Retrieval hit ratio mất dữ liệu |

Ghi chú: ngay ở tải nền (30 user, model nhỏ) `LLMCostBurnRateHigh` **đã firing** vì chi phí ước tính $2.45/giờ vượt ngưỡng $2/giờ. Ngưỡng này hợp lý cho lab nhưng trong thực tế cần đặt theo budget thật và lưu lượng dự kiến, nếu không alert sẽ kêu liên tục (alert fatigue).

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đổi sang v1 làm accuracy rơi từ 100 % xuống 4.3 % (eval ở mục 3), nhưng latency, error rate, instance đều "xanh", vì metric hạ tầng chỉ đo agent có trả lời **nhanh và không lỗi**, không đo trả lời **đúng**. Cách phát hiện:

1. **Chặn trước khi deploy:** quality gate trong CI (`run_eval.py` với `--baseline`) trả exit code 1 cho v1 (đã thử ở mục 3), nên bản này không được merge.
2. **Giám sát chất lượng khi đang chạy:** chấm điểm tự động một mẫu nhỏ câu trả lời thật (LLM-as-judge hoặc keyword như golden dataset), thu feedback người dùng (tỉ lệ 👎), đưa thành metric và đặt alert khi giảm.
3. **Alert trên metric gián tiếp:** retrieval hit ratio về 0, tokens/request giảm đột ngột, hoặc `agent_info{version}` đổi ngoài lịch deploy.

Tương tự, thí nghiệm D cho thấy lỗi 504 xảy ra ở Nginx (request chưa bao giờ được agent xử lý xong) nên metric của agent không đếm. Cần thêm metric ở tầng load balancer hoặc synthetic check gọi từ bên ngoài.

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: `c2366118e2b26bd8a08c7fd32d7a34af` (lúc chạy 1 instance, 100 user)
- Tổng thời gian: 2334.1 ms
- Span chiếm nhiều thời gian nhất: `chat mock-llm-small` (1653.0 ms, 70.8 %)
- Các phần còn lại: `agent.retrieve_context` 191.1 ms (8.2 %, bình thường ~20 ms); khoảng ~490 ms (21 %) từ lúc `POST /ask` bắt đầu tới lúc `agent.run` chạy, tức request phải xếp hàng chờ event loop vì 1 instance (giới hạn 0.5 CPU) đang gánh 100 user.
- Kết luận: phần lớn thời gian là LLM upstream chậm (request rơi vào nhóm "slow" của mock, `llm_ms` = 1652.1), không phải code của agent; phần còn lại do instance quá tải, nên scale ngang sẽ giảm được (xem mục 5).
- Dòng log tương ứng (grep theo `trace_id`):

```json
{"ts": "2026-11-26T17:12:23.844049+00:00", "level": "INFO", "service": "observable-agent", "instance": "818b4c282e01", "event": "llm_call", "trace_id": "c2366118e2b26bd8a08c7fd32d7a34af", "span_id": "2d65523d9f10ac73", "request_id": "18ef184d1a3d", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 74, "output_tokens": 37, "cost_usd": 3.3e-05, "llm_ms": 1652.1}
{"ts": "2026-11-26T17:12:23.845371+00:00", "level": "INFO", "service": "observable-agent", "instance": "818b4c282e01", "event": "http_request", "trace_id": "c2366118e2b26bd8a08c7fd32d7a34af", "span_id": "2d65523d9f10ac73", "request_id": "18ef184d1a3d", "method": "POST", "route": "/ask", "status": 200, "ms": 2333.1}
```

`ms` trong log (2333.1) khớp với độ dài trace (2334.1 ms).

Trace lỗi `8c80383129abf9a6d70a322fcd129485` (114 ms, 503) và dòng log tương ứng:

```json
{"ts": "2026-11-26T17:23:31.200289+00:00", "level": "INFO", "service": "observable-agent", "instance": "c35215ef717f", "event": "llm_error", "trace_id": "8c80383129abf9a6d70a322fcd129485", "span_id": "cbb8ebc10c89069a", "request_id": "8b4c01c03d5d", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

**Lưu ý khi đọc trace:** trace "dài nhất" Jaeger trả về ban đầu (`0c366550de0f86a7ffa7bfb6a7d8c93f`, 2687.8 ms) là **số đo sai**. Log của chính request đó ghi `ms: 212.4` và `llm_ms: 186.6`. Lý do: span của OpenTelemetry tính bằng đồng hồ hệ thống (wall clock), lúc đó đồng hồ máy ảo Docker bị đồng bộ lại và nhảy ~2.4 s; còn log đo bằng `time.perf_counter()` (đồng hồ đơn điệu) nên không bị ảnh hưởng. Vì vậy tôi chọn trace có độ dài khớp với `ms` trong log (767 trace khớp trong ngưỡng 5 ms).

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 62.86 | 210 | 890 | 1700 | 76 / 7478 (1.02 %) |
| 3 | 65.74 | 200 | 270 | 1700 | 53 / 7816 (0.68 %) |

Số liệu từ dòng `Aggregated` của `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv`. Lệnh: `docker compose -p k5-day14-observability run --rm --no-deps locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_<n>`. Failures đều là `503` do lỗi upstream giả lập của mock LLM (`MOCK_ERROR_RATE=0.01`).

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

- **p95 cải thiện rõ: 890 → 270 ms (giảm 70 %).** Với 1 instance (giới hạn 0.5 CPU), 100 user làm event loop quá tải: request phải xếp hàng, thấy rõ trong trace ở mục 4.3 (~490 ms chờ, retrieval 191 ms thay vì ~20 ms). Có 3 instance thì mỗi instance chỉ gánh ~1/3 tải, hết xếp hàng.
- **RPS gần như không đổi (62.86 → 65.74)** vì giới hạn nằm ở **phía tạo tải**, không phải agent. Locust là "closed loop": mỗi user gửi request, chờ trả lời, rồi nghỉ `wait_time = between(0.5, 2)` (trung bình 1.25 s). Throughput tối đa ≈ 100 user / (1.25 s + latency) ≈ 100 / 1.54 ≈ 65 req/s với 1 instance và ≈ 100 / 1.48 ≈ 68 req/s với 3 instance, khớp số đo. Muốn đo năng lực tối đa phải tăng số user hoặc bỏ `wait_time`.
- **p99 không đổi (1700 ms)** vì đuôi latency đến từ **LLM upstream**: ~3 % request bị mock LLM chậm thêm 1.5 s ở bất kỳ instance nào. Panel LLM latency p95 đứng yên ~195–199 ms trong cả 2 lần chạy. Thêm instance không làm LLM nhanh hơn; muốn giảm p99 phải xử lý ở tầng LLM (timeout + retry sang request khác, model nhanh hơn, cache câu trả lời, streaming).
- **Kết luận:** scale ngang giải quyết nút thắt **CPU/xếp hàng của agent** (p95), không giải quyết được nút thắt **LLM upstream** (p99).

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Đặt SLO theo percentile, không theo average.** Ở mục 2.2, 10 % request chậm đẩy p95 lên 1692 ms nhưng avg chỉ 326 ms, vẫn "đạt SLO". p99 phát hiện tail latency sớm nhất.
  2. **Metric chỉ thấy những gì chính service đó nhìn thấy.** Lỗi 504 ở Nginx (thí nghiệm D) và lỗi chất lượng khi đổi sang v1 (thí nghiệm E) đều không làm dashboard của agent đỏ. Cần thêm eval chất lượng, metric ở load balancer và kiểm tra từ bên ngoài.
  3. **Phải kiểm tra lại chính phép đo.** Lệnh benchmark mẫu âm thầm chạy sai số instance, và một trace bị phóng đại vì đồng hồ hệ thống nhảy. Chỉ phát hiện được nhờ đối chiếu nhiều nguồn (`docker events` với log Locust, độ dài span với `ms` trong log). Số liệu không được kiểm chứng thì không nên đưa vào báo cáo.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Metric ở tầng load balancer:** tỉ lệ 5xx/504 và latency upstream của Nginx, kèm synthetic check gọi `/ask` từ bên ngoài mỗi phút.
  - **Chất lượng câu trả lời:** điểm LLM-as-judge trên mẫu request thật, tỉ lệ feedback tiêu cực, retrieval hit ratio; alert khi giảm so với baseline.
  - **Alert theo tốc độ tiêu error budget** (multi-window burn rate, ví dụ 1 giờ và 6 giờ) thay vì ngưỡng cố định, để vừa bắt sự cố nhanh vừa ít báo giả.
  - **Saturation thật của container:** CPU throttling, memory (qua cAdvisor), độ trễ event loop; vì p95 xấu đi ở 1 instance là do CPU chứ không phải do LLM.
  - **Phía bảo vệ của agent Part 6:** tỉ lệ 429 (rate limit), 402 (hết budget), budget còn lại trong ngày, trạng thái kết nối Redis.
