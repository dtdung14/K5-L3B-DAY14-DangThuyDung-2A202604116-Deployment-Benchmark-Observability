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

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals)
- [ ] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian)
- [ ] Một trace lỗi trong Jaeger (có event `exception`)
- [ ] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | | | |
| B | `MOCK_SLOW_RATE=0.3` | | | |
| C | `LLM_MODEL=mock-llm-large` | | | |
| D | `docker pause` 1 instance | | | |
| E | `AGENT_VERSION=v1` | | | |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

...

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: ...
- Tổng thời gian: ... ms
- Span chiếm nhiều thời gian nhất: ... (... ms, ...%)
- Dòng log tương ứng (grep theo `trace_id`):

```json

```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | | | | | |
| 3 | | | | | |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

...

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. ...
  2. ...
  3. ...
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

...
