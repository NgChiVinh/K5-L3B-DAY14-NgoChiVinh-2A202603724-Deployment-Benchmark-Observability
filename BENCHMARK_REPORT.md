# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ report JSON trong [`reports/`](reports/) và CSV trong [`benchmark-results/`](benchmark-results/), không ước lượng.

**Họ tên:** Ngô Chí Vĩnh — 2A202603724
**Ngày chạy:** 2026-11-26
**Môi trường:** Laptop Windows 11, Intel Core Ultra 9 185H (22 luồng), RAM 32 GB, Python 3.12.10. Phần develop: agent chạy 1 process uvicorn trên chính máy chạy load test (load generator và agent dùng chung CPU).

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | LLM mock mất khoảng 120–220 ms mỗi call, cộng retrieval khoảng 20 ms, nên request bình thường khoảng 200 ms. Ngưỡng 500 ms chừa khoảng 2,5 lần headroom cho lúc tải cao mà người dùng vẫn thấy phản hồi nhanh. Đặt theo p95 chứ không theo average vì average che mất tail latency (mục 2.2). |
| Error rate `/ask` | < 2 % | Upstream LLM mock lỗi ngẫu nhiên khoảng 1 % (`MOCK_ERROR_RATE=0.01`). Ngưỡng 2 % là error budget gấp đôi mức lỗi nền: vượt mức đó là có sự cố thật chứ không phải nhiễu. |
| Accuracy trên golden dataset | ≥ 85 % | Bằng ngưỡng quality gate trong CI ([`.github/workflows/eval.yml`](.github/workflows/eval.yml)). v2 ban đầu đạt 91,3 % nên vượt ngưỡng; v1 chỉ đạt 4,3 % nên bị chặn. Để 85 % thay vì 100 % vì chấm bằng keyword có nhiễu, đòi 100 % trong CI sẽ hay fail oan. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

Lệnh: `python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json`. SLO kiểm tra là mặc định của script: p95 ≤ 500 ms, error rate ≤ 2 %.

### 2.1 Concurrency vs latency (agent v2)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.45 | 194.2 | 227.2 | 1680.8 | 1.33 % (4 × 503) | ✅ PASS |
| 10 | 32.63 | 187.2 | 451.5 | 1691.5 | 0.00 % | ✅ PASS |
| 50 | 93.56 | 208.8 | 530.3 | 1734.0 | 1.67 % (5 × 503) | ❌ FAIL (p95) |
| 100 | 57.36 | 618.6 | 3079.6 | 3378.7 | 0.00 % | ❌ FAIL (p95) |

Nguồn: `reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`.

**Nhận xét:** RPS **không** tăng tuyến tính theo concurrency.

- **c = 1 → 10:** RPS tăng từ 4,45 lên 32,63, tức khoảng 7,3 lần chứ không phải 10 lần. Với 1 client, RPS ≈ 1 / latency (1 / 0,226 s ≈ 4,4). Khi có 10 client, mỗi request chậm bất thường (~1,7 s) giữ chặt 1 slot rất lâu, kéo throughput chung xuống.
- **c = 10 → 50:** RPS lên 93,56, là mức đỉnh. p50 gần như không đổi (187 → 209 ms), nhưng p95 vượt 500 ms. Các request đã bắt đầu phải xếp hàng.
- **c = 50 → 100:** RPS **giảm** còn 57,36, còn p50 tăng gấp 3 (618,6 ms) và p95 lên 3079,6 ms. Agent đã **bão hoà**. Agent chạy 1 process uvicorn với 1 event loop, phải tự serialize/parse JSON và ghi log cho mọi request. Load generator lại chạy cùng máy và tranh CPU với agent. Thêm client lúc này chỉ làm hàng đợi dài ra (theo định luật Little: latency = concurrency / throughput) chứ không tăng throughput. Ngoài ra 300 request ở c = 100 chỉ là khoảng 3 lượt mỗi client, cả lần đo chỉ dài 5,23 s, nên tail và giai đoạn khởi động chiếm tỉ trọng lớn. Muốn số ổn định hơn ở mức tải này thì nên chạy `--duration 30`.
- **Lỗi 503:** đến từ lỗi upstream giả lập (khoảng 1 %), không phụ thuộc tải; ở c = 100 có 0 lỗi.
- **Kết luận:** một instance v2 giữ được SLO tới khoảng c = 10–20. Muốn chịu c ≥ 50 phải scale ngang (nhiều worker hoặc instance, xem mục 5).

### 2.2 Tail latency

Bật lại v2 với từng giá trị `MOCK_SLOW_RATE`, mỗi lần chạy `--requests 300 --concurrency 20`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 202.1 | 436.8 | 459.0 |
| 0.03 (mặc định) | 275.3 | 507.2 | 1705.9 |
| 0.1 | 330.9 | 1688.1 | 1728.5 |

Nguồn: `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`.

**Nhận xét:**

- **Chỉ số nhạy nhất là p99.** Từ 0 lên 0,03, p99 tăng từ 459,0 lên 1705,9 ms, gấp khoảng 3,7 lần. Cùng lúc đó avg chỉ tăng 202,1 → 275,3 ms (+36 %), còn p95 gần như không đổi.
  - 3 % request chậm nằm hoàn toàn ngoài ngưỡng 95 %, nên chỉ p99 "nhìn thấy" chúng.
  - Khi tỉ lệ chậm lên 10 % (vượt 5 %), p95 cũng nhảy vọt lên 1688,1 ms, trong khi avg vẫn chỉ 330,9 ms.
- **Vì sao không đặt SLO theo average:** average trộn vài request rất chậm vào nhiều request nhanh nên bị "pha loãng". Ở `MOCK_SLOW_RATE=0.1`, avg 331 ms trông vẫn ổn, nhưng thực tế cứ 10 người dùng thì 1 người phải chờ khoảng 1,7 s. SLO phải phản ánh trải nghiệm của nhóm người dùng tệ nhất, nên đặt theo p95/p99.
- **RPS cũng giảm theo:** 97,29 → 56,81 → 49,27. Request chậm chiếm slot concurrency lâu hơn, nên ảnh hưởng cả throughput chứ không chỉ latency.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3 % (1/23) | 91.3 % (21/23) | 100 % (23/23) |
| Avg tokens / câu | 56.9 | 109.0 | 116.5 |
| Tổng cost (USD) | 0.000401 | 0.000760 | 0.000815 |
| Cost / câu đúng (USD) | 0.00040095 | 0.00003619 | 0.00003543 |
| Quality gate | ❌ FAIL (min 80 %) | ✅ PASS (min 80 %, 0 regression so với v1) | ✅ PASS (min 100 %, 0 regression so với v2) |

Nguồn: `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json`.

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
| `scaling-03` | `cpu`, `memory`, `custom metrics` | Thêm tài liệu về Kubernetes HPA vào `KNOWLEDGE_BASE`, kích hoạt bởi `hpa` / `kubernetes` / `autoscal`: HPA scale số pod theo CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi). |
| `security-02` | `token`, `hết hạn`, `chữ ký` | Thêm tài liệu so sánh JWT với API key, kích hoạt bởi `jwt` / `json web token`: JWT là token có chữ ký số, chứa thông tin user và thời điểm hết hạn; API key là chuỗi cố định, không tự hết hạn. |

Lý do cả 2 câu sai: retrieval không tìm được tài liệu nào, nên agent rơi về câu trả lời chung chung. Từ khoá kích hoạt mới chỉ xuất hiện trong đúng 2 câu này (đã kiểm tra cả golden dataset), nên không câu nào khác bị đổi tài liệu retrieve. Kết quả: 2 câu được cải thiện, 0 regression. Phần sửa được chép sang `production/utils/mock_llm.py` để stack Docker ở Bước 2 cũng đạt 100 %.

**Quality gate trong CI:**
- Chạy eval v1 với `--baseline reports/eval_v2.json`: script báo 20 câu regression, accuracy 4,3 % và trả **exit code 1**.
- Job [`.github/workflows/eval.yml`](.github/workflows/eval.yml) chạy trên mỗi pull request: bật agent v2 rồi chạy `run_eval.py --min-accuracy 0.85`. Exit code khác 0 làm job fail. Bật branch protection "Require status checks to pass" thì PR không merge được.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- **Chất lượng:** v2 có bước retrieval (RAG), accuracy tăng từ 4,3 % lên 91,3 %.
- **Chi phí:** prompt dài hơn vì có context, nên v2 tốn gần gấp đôi token mỗi câu (56,9 → 109,0) và gấp đôi tổng cost (0,000401 → 0,000760 USD).
- **Latency:** chậm hơn một chút, p50 eval từ 159,5 lên 178,2 ms, do thêm khoảng 20 ms retrieval và prompt dài hơn.
- **Cost / câu đúng:** v2 **rẻ hơn khoảng 11 lần** (0,00040095 → 0,00003619 USD). Cost / câu đúng là chỉ số đúng để so sánh vì v1 rẻ nhưng gần như không trả lời đúng câu nào: tiền bỏ ra không mua được kết quả.
- **Sau khi sửa KB:** tốn thêm 7,5 token/câu vì tài liệu mới dài hơn, nhưng cost / câu đúng còn giảm thêm (0,00003543).
- **Lưu ý về latency trong eval:** p95 của lần eval cuối là 1541,5 ms. Đây là nhiễu do mẫu nhỏ chứ không phải do phần sửa: chỉ 23 câu thì 1–2 request rơi vào nhóm chậm 3 % là p95 nhảy vọt. Vì vậy latency phải đo bằng load test (mục 2), không đo bằng eval.

---

## 4. Observability stack (production)

> Phần này làm ở CP8 (Docker stack Prometheus / Grafana / Jaeger), sau giờ lab.

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): `screenshots/grafana-dashboard.png`
- [ ] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian): `screenshots/jaeger-slow-trace.png`
- [ ] Một trace lỗi trong Jaeger (có event `exception`): `screenshots/jaeger-error-trace.png`
- [ ] Trang Alerts của Prometheus với ít nhất 2 alert đang **firing**: `screenshots/prometheus-alert-firing.png`

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
