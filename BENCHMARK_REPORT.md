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

Bật stack bằng `docker compose -p k5-day14-observability up -d --build --scale agent=1`; Grafana, Prometheus, Jaeger và Nginx chạy cùng Locust.

**Sửa lỗi có sẵn trong đề.** Ban đầu agent crash liên tục với lỗi `ModuleNotFoundError: No module named 'uvicorn'`:
- Trong `production/Dockerfile`, user `agent` được tạo với `HOME=/app` (`useradd -d /app`), nên Python tìm user site-packages ở `/app/.local`.
- Nhưng các package lại được copy vào `/home/agent/.local`, nên Python không import được.
- Tôi thêm `ENV PYTHONUSERBASE=/home/agent/.local`, giống cách Dockerfile của Part 5 đã làm.

Unit test alert rules:

```text
$ docker run --rm -v "$PWD/prometheus:/p" --entrypoint promtool prom/prometheus:v3.16.0 test rules /p/alert_rules_test.yml
Processing test file: /p/alert_rules_test.yml
  SUCCESS
```

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png). Chụp lúc đang chạy A + B cùng lúc: Traffic 15.5 req/s, Error rate 23.15 % (đỏ), p95 1.89 s (đỏ), 2 instances up.
- [x] Một trace chậm trong Jaeger: [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png), xem mục 4.3.
- [x] Một trace lỗi trong Jaeger (có event `exception`): [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png), trace `c63a04790d0c6bd376a59dffa30565fc`.
- [x] Trang Alerts của Prometheus với ≥ 2 alert **firing**: [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png). `AgentHighErrorRate` và `AgentHighLatencyP95` cùng FIRING.

**Trả lời Exercise 7.4.** Đo bằng Prometheus khi stack chạy cấu hình mặc định, 2 instance, Locust 30 user:

1. **p95 và avg đang là bao nhiêu? Khoảng cách nói lên điều gì?**
   - p95 = 0.293 s, avg = 0.231 s, p50 = 0.179 s, nhưng p99 = 1.817 s.
   - p95 và avg gần nhau vì đa số request bình thường. Khoảng cách lớn nằm ở p99: khoảng 3 % request bị LLM làm chậm khoảng 1.5 s.
   - Avg không lộ ra đuôi chậm này. Khi tỉ lệ chậm lên 30 % (thí nghiệm B), p95 vọt lên 1.91 s trong khi avg chỉ 0.62 s.
2. **Nginx có chia đều traffic cho 2 instance không?** Có. `sum by (instance)(rate(http_requests_total{route="/ask"}[1m]))` cho 172.19.0.4 là 9.33 req/s, 172.19.0.8 là 9.26 req/s (round-robin). Khi scale lên 3, `X-Served-By` lần lượt đi qua cả 3 IP.
3. **Vì sao input tokens/s lớn hơn output tokens/s?** Input 1460 tokens/s so với output 773 tokens/s. Prompt của v2 gồm system prompt, tài liệu retrieve được *và* câu hỏi, còn câu trả lời chỉ là 1 đoạn tài liệu. RAG làm prompt dài ra, nên input luôn lớn hơn output, và giá input/output khác nhau cũng ảnh hưởng tới cost.
4. **`curl http://localhost:8080/metrics` trả về gì?**
   - Trả `HTTP/1.1 404 Not Found` (trang lỗi của nginx/1.28.3), vì `nginx.conf` có `location = /metrics { return 404; }`.
   - Metrics lộ thông tin nội bộ (route, lưu lượng, model, chi phí) nên không public ra ngoài.
   - Prometheus vẫn scrape được vì nó gọi thẳng `agent:8000/metrics` trong mạng nội bộ Docker, không đi qua Nginx.

### 4.2 Thí nghiệm "phá hệ thống"

Cách đo:
- Locust chạy nền 30 user suốt các thí nghiệm.
- Mỗi thay đổi áp dụng bằng `<BIẾN>=<giá trị> docker compose -p k5-day14-observability up -d --scale agent=2 agent` (tạo lại container agent).
- Script đọc `GET /api/v1/alerts` của Prometheus mỗi 2 s, ghi lại thời điểm alert chuyển `pending` và `firing`, tính từ lúc chạy lệnh.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (giá trị 18.4 % khi firing) | pending sau **26.8 s** (error rate vượt 5 %), **firing sau 87.0 s** (thêm `for: 1m`) | *Error rate* chuyển đỏ; *Requests/s theo status code* thấy đường 503 tăng; Jaeger có trace lỗi |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (p95 = 1.91 s khi firing) | pending sau **24.7 s**, **firing sau 145.1 s** (`for: 2m`) | *Latency p95* đỏ; *Latency p50/p95/p99*: p95 1.91 s và p99 1.98 s nhảy vọt trong khi avg chỉ 0.62 s |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh`. **Đã firing từ trước thí nghiệm** (xem ghi chú) và tiếp tục firing, giá trị 5 phút lên $20.6/giờ sau 3 phút | Không đo được "đổi → firing" vì đã firing sẵn. Cost/giờ theo cửa sổ 1 phút đạt mức mới sau khoảng 60 s: **$2.47 → $35.7/giờ (≈ 14.5 lần)** | *Cost burn rate (USD/giờ)* và *LLM cost (1h)*; *LLM latency p95 theo model*: 0.197 s → 0.495 s (≈ 2.5 lần). p95 toàn request 0.67 s (< 1 s) nên alert latency **không** firing |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` (instance 172.19.0.4) | pending sau **10.5 s** (scrape đầu tiên fail), **firing sau 40.6 s** (`for: 30s`) | *Instances up* giảm từ 2 xuống 1. *Error rate* của agent vẫn **xanh** (0.68 %), trong khi Locust thấy request treo tới **10 004 ms**, tức 504 do Nginx hết `proxy_read_timeout 10s` |
| E | `AGENT_VERSION=v1` | **Không có** | — | Bảng *version* đổi sang v1; *retrieval hit ratio* mất dữ liệu (v1 không retrieve); tokens/s giảm khoảng một nửa (input 1396 → 683/s); cost/giờ $2.36 → $1.17; p95 **còn tốt hơn** (0.295 → 0.199 s) |

Ghi chú:
- **Ngưỡng $2/giờ của `LLMCostBurnRateHigh` quá thấp so với tải lab.** Cấu hình mặc định (v2, model small) với 30 user, khoảng 18.6 req/s, đã tốn **$2.47/giờ**, nên alert này firing ngay cả khi hệ thống bình thường. Trong production phải đặt ngưỡng theo ngân sách thật và theo lưu lượng dự kiến, nếu không sẽ gây "alert fatigue".
- Lúc tạo lại container agent, `AgentInstanceDown` có chuyển **pending** vài giây (IP cũ không còn trả lời trong một lượt scrape) nhưng **không firing**, nhờ `for: 30s`. Đây đúng là tác dụng của `for`: chặn báo động giả khi deploy.
- Sau thí nghiệm D đã `docker unpause`. Mọi thay đổi đều áp dụng qua biến môi trường của lệnh, không sửa `.env`. Cuối cùng stack được trả về cấu hình mặc định.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đổi sang v1 làm *chất lượng* sụp đổ: v1 trả lời câu "Kubernetes HPA scale dựa trên những chỉ số nào?" bằng câu chung chung "Đây là câu trả lời từ AI agent (mock)...". Eval ở mục 3 cho accuracy 4.3 %. Nhưng mọi metric hạ tầng lại *đẹp hơn*: rẻ hơn, nhanh hơn, không có lỗi. Phải giám sát chất lượng bằng tín hiệu riêng:

1. **Quality gate trước khi deploy:** chạy `run_eval.py` trên golden dataset trong CI (đã viết `.github/workflows/eval.yml`). Accuracy thấp hơn ngưỡng hoặc có regression thì exit code 1 và chặn merge. Bản v1 sẽ không bao giờ lên production.
2. **Eval online sau deploy:** định kỳ (ví dụ mỗi 5 phút) gửi một phần golden dataset vào production, hoặc lấy mẫu khoảng 1 % câu trả lời thật cho LLM-as-judge chấm. Xuất điểm ra metric `agent_eval_accuracy` rồi đặt alert, ví dụ `< 0.85 for 10m`.
3. **Alert trên metric gián tiếp của chất lượng:** retrieval hit ratio giảm mạnh, ví dụ `< 0.5`, hoặc tokens/câu trả lời giảm đột ngột so với tuần trước.
4. **Tín hiệu từ người dùng:** tỉ lệ 👎, tỉ lệ hỏi lại cùng câu, tỉ lệ chuyển sang người hỗ trợ.
5. **Canary deploy:** cho bản mới nhận một phần nhỏ traffic, so sánh điểm eval và feedback với bản cũ rồi mới tăng dần.

### 4.3 Debug bằng trace

Trace chậm nhất, chụp lúc chạy thí nghiệm B. Tìm bằng cách lấy dòng log `llm_call` có `llm_ms` lớn nhất rồi mở `trace_id` đó trong Jaeger:

- `trace_id`: `a1487ca05678d4fcf3261a5bfd9eaadc`
- Tổng thời gian: **1717.9 ms** (span gốc `POST /ask`)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small`** (1698.5 ms, **98.9 %**). Đây là call LLM; `agent.retrieve_context` chỉ 17.4 ms (1.0 %). Nghĩa là chậm vì upstream LLM, không phải vì code của agent.

| Span | Thời gian | % |
|---|---|---|
| `POST /ask` | 1717.9 ms | 100 % |
| └ `agent.run` | 1716.1 ms | 99.9 % |
| &nbsp;&nbsp;&nbsp;├ `agent.retrieve_context` | 17.4 ms | 1.0 % |
| &nbsp;&nbsp;&nbsp;└ `chat mock-llm-small` (`gen_ai.usage.input_tokens=95`, `output_tokens=55`) | 1698.5 ms | 98.9 % |

- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep a1487ca05678d4fcf3261a5bfd9eaadc`):

```json
{"ts": "2026-11-26T09:05:26.602155+00:00", "level": "INFO", "service": "observable-agent", "instance": "7d4a91c63668", "event": "llm_call", "trace_id": "a1487ca05678d4fcf3261a5bfd9eaadc", "span_id": "38255b3a79d80336", "request_id": "8dec600abd81", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 95, "output_tokens": 55, "cost_usd": 4.7e-05, "llm_ms": 1698.4}
```

Trace lỗi, chụp lúc chạy thí nghiệm A:
- `trace_id` là `c63a04790d0c6bd376a59dffa30565fc`, tổng thời gian 107.1 ms.
- Span `chat mock-llm-small` (88.3 ms) và `agent.run` đều có `error=true` cùng event `exception`: `utils.mock_llm.LLMError: Upstream LLM error: 503 Service Unavailable (mock)`.
- Dòng log cùng `trace_id`:

```json
{"ts": "2026-11-26T09:03:29.987175+00:00", "level": "INFO", "service": "observable-agent", "instance": "783f93913849", "event": "llm_error", "trace_id": "c63a04790d0c6bd376a59dffa30565fc", "span_id": "9dd889163b5aed44", "request_id": "212042faab00", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Lệnh: `docker compose -p k5-day14-observability run --rm locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_<n>`. Số liệu lấy từ dòng `/ask` của [`benchmark-results/scale_1_stats.csv`](benchmark-results/scale_1_stats.csv) và [`benchmark-results/scale_3_stats.csv`](benchmark-results/scale_3_stats.csv).

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.67 | 190 | 240 | 1700 | 76 / 7088 (1.07 %, đều là 503) |
| 3 | 59.74 | 190 | 230 | 1700 | 78 / 7102 (1.10 %, đều là 503) |

(Dòng `Aggregated` gồm cả `/health`: 66.00 và 66.06 req/s.)

**Giải thích kết quả: scale có cải thiện p95 không? Nút thắt nằm ở đâu?**

**Gần như không.**
- RPS 59.67 → 59.74, p95 240 → 230 ms, p99 giữ 1700 ms. Tỉ lệ lỗi giữ khoảng 1 %, chính là `MOCK_ERROR_RATE=0.01`, không phụ thuộc số instance.

Nút thắt không nằm ở agent:
1. **Tải do Locust tạo ra có giới hạn trên.** Mỗi user chờ 0.5–2 s (trung bình 1.25 s) giữa các request và chờ phản hồi khoảng 0.21 s. Vì vậy 100 user chỉ tạo được tối đa khoảng 100 / 1.46 ≈ 68 req/s, khớp với 66 req/s đo được.
   - 1 instance đã xử lý hết lượng tải này mà chưa bão hoà, vì agent là async và phần lớn thời gian chỉ *chờ* LLM (I/O), không tốn CPU.
   - Thêm instance không có gì để chia thêm.
2. **Latency bị chặn dưới bởi LLM.** p50 190 ms gần bằng latency cơ bản của mock LLM (120 ms + jitter + token). p99 1700 ms đến từ 3 % request chậm của upstream (`MOCK_SLOW_RATE`). Panel *LLM latency p95* cho thấy gần như toàn bộ thời gian là của LLM; scale ngang agent không làm LLM nhanh hơn.

Muốn thấy lợi ích của scale phải đẩy agent tới bão hoà: tăng số user, giảm `wait_time`, hoặc giới hạn CPU thấp hơn. Ở develop (1 process, mục 2.1), RPS *giảm* khi concurrency lên 100, chính là lúc thêm instance mới có ích. Trong thực tế, muốn giảm latency của agent LLM nên dùng model nhanh hơn, cache, streaming, hoặc timeout + retry/hedging cho request chậm, chứ không phải thêm instance.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Average nói dối, percentile thì không.** Ở `MOCK_SLOW_RATE=0.1`, avg 331 ms trông ổn nhưng p95 là 1688 ms. SLO và alert phải đặt theo p95/p99, và phải đo bằng load test đủ mẫu (23 câu eval quá ít để kết luận về latency).
  2. **Metric hạ tầng chỉ thấy những gì *chính service đó* thấy.**
     - Thí nghiệm D: Nginx trả 504 nhưng error rate của agent vẫn xanh.
     - Thí nghiệm E: chất lượng sụp nhưng mọi panel còn đẹp hơn.
     - Vì vậy cần giám sát nhiều tầng: load balancer, synthetic check từ bên ngoài, và eval chất lượng.
  3. **Metrics, traces và logs phải nối được với nhau qua `trace_id`.** Dashboard báo p95 tăng → Jaeger chỉ ra span `chat mock-llm-small` chiếm 98.9 % → log `llm_call` cùng `trace_id` cho biết model, số token và chi phí. Từ "có vấn đề" tới "vấn đề ở đâu" chỉ mất vài bước.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Chất lượng:** metric `agent_eval_accuracy` từ eval online định kỳ; alert khi `< 0.85 for 10m`. Alert khi retrieval hit ratio `< 0.5`.
  - **Tầng ngoài agent:** error rate và latency do Nginx đo (`nginx_http_requests_total{status=~"5.."}`, đặc biệt 502/504), cùng synthetic check gọi `/ask` từ bên ngoài mỗi phút (blackbox exporter).
  - **Cost theo ngân sách thật:** alert theo % ngân sách ngày (ví dụ đã tiêu > 80 % `DAILY_BUDGET_USD`), cùng burn rate theo nhiều cửa sổ (5 phút và 1 giờ) để vừa bắt spike vừa bắt xu hướng.
  - **SLO error budget burn rate:** alert khi tốc độ tiêu error budget nhanh hơn mức cho phép (multi-window, multi-burn-rate), thay vì ngưỡng cứng.
  - **Saturation:** `agent_in_flight_requests` gần giới hạn concurrency, CPU/memory của container gần limit; rate limit 429 tăng đột biến (có thể bị tấn công, hoặc cần nâng quota).
  - **Phía LLM provider:** tỉ lệ 429/timeout từ upstream, latency p99 theo model, số retry.
