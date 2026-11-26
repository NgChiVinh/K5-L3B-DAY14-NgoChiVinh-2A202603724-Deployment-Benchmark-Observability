# Day 14 Lab — Mission Answers

> Môi trường: Windows 11, Docker Desktop 29.4.3 (Compose v5.1.3), Python 3.12.10. Các bài cần SIGTERM (5.2, script chấm Part 6) chạy trong container hoặc WSL Linux.
> Mọi output dưới đây là output thật từ lệnh đã chạy ngày 2026-11-26. Token và key đã được che.

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found

Trong `01-localhost-vs-production/develop/app.py`:

| # | Anti-pattern | Dòng | Hậu quả |
|---|---|---|---|
| 1 | Hardcode secret: `OPENAI_API_KEY = "sk-hardcoded-..."` | 17 | Push lên GitHub là lộ key ngay, kể cả xoá sau đó vẫn nằm trong lịch sử git |
| 2 | Hardcode connection string có mật khẩu DB: `postgresql://admin:password123@...` | 18 | Lộ mật khẩu database; muốn đổi DB phải sửa code |
| 3 | Config cố định trong code: `DEBUG = True`, `MAX_TOKENS = 500` | 21–22 | Không đổi được giữa dev / staging / prod mà không build lại; debug bật cả ở production |
| 4 | Log bằng `print()` và **log cả secret**: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | 33–38 | Key xuất hiện trong log của platform; log dạng text khó tìm kiếm / lọc |
| 5 | Không có endpoint `/health` (gọi thử trả 404) | 42 | Platform không biết app chết để restart, load balancer không biết để bỏ qua |
| 6 | Bind `host="localhost"` | 51 | Trong container chỉ nhận kết nối từ chính container → từ ngoài không gọi được |
| 7 | Port cứng `port=8000` | 52 | Railway / Render cấp port qua biến `PORT`, app không nghe đúng port → deploy fail |
| 8 | `reload=True` | 53 | Chế độ dev: tốn tài nguyên theo dõi file, không dùng cho production |
| 9 | Không xử lý shutdown (SIGTERM) | — | Khi redeploy / scale down, request đang chạy có thể bị cắt ngang |
| 10 | Input qua query string, không giới hạn độ dài, không auth | 31 | Ai cũng gọi được, câu hỏi dài tuỳ ý → tốn tiền LLM |

### Exercise 1.2: Chạy basic version

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found
{"detail":"Not Found"}

$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]}  -> HTTP 422
```

Log của server (stdout), thấy rõ secret bị in ra:

```text
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [22912] using WatchFiles
```

**Quan sát:** app "chạy được" nhưng không production-ready: không có health check, log lộ key, chỉ nghe `localhost`, chạy reloader của chế độ dev.

### Exercise 1.3: Comparison table

Chạy bản production (`cp .env.example .env`, `python app.py`):

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":3.6,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T08:17:44.714998+00:00"}

$ curl http://localhost:8000/ready
{"ready":true}  -> HTTP 200

$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). ...","model":"gpt-4o-mini"}  -> HTTP 200

$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{}'
{"detail":"question field is required"}  -> HTTP 422
```

| Feature | Basic (develop) | Advanced (production) | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (key, DB URL, DEBUG, port) | Đọc từ env qua `config.py` (`os.getenv`), có `validate()` fail-fast khi thiếu `AGENT_API_KEY` ở production | Cùng một image chạy được ở mọi môi trường; secret không nằm trong git; đổi cấu hình không cần build lại |
| Secrets | `sk-...` và mật khẩu DB trong code, còn bị `print` ra log | Không có secret trong code, không log secret (chỉ log `question_length`) | Lộ key = mất tiền / mất dữ liệu; log thường được nhiều người và nhiều hệ thống đọc |
| Health check | Không có (404) | `/health` (liveness) + `/ready` (readiness, 503 khi chưa sẵn sàng) | Platform restart khi app treo; load balancer chỉ gửi traffic vào instance đã sẵn sàng |
| Logging | `print()` dạng text | Log có cấu trúc JSON qua `logging` | Log aggregator (Loki, Datadog) parse và lọc theo field được, ví dụ lọc theo `event` |
| Shutdown | Đột ngột, không xử lý SIGTERM | `lifespan` có phần shutdown + handler SIGTERM, uvicorn chờ request đang chạy | Redeploy / scale down không làm rơi request của người dùng |
| Host / Port | `localhost:8000` cố định, `reload=True` | `0.0.0.0` + `PORT` từ env, reload chỉ khi `DEBUG=true` | Chạy được trong container và trên Railway / Render (port do platform cấp) |
| Input | Query param, không validate | JSON body, thiếu `question` → 422 rõ ràng | Lỗi đầu vào trả mã rõ, không đi tới LLM |

**Phát hiện thêm khi chạy bản production:** log JSON của app **không hề xuất hiện**, chỉ có log của uvicorn.
- **Nguyên nhân:** `config.py` gọi `logging.warning("OPENAI_API_KEY not set ...")` ngay lúc import, tức là *trước* `logging.basicConfig(...)` trong `app.py`. Lệnh đó tự gắn handler mặc định vào root logger, nên `basicConfig` sau đó không có tác dụng. Level vẫn là WARNING, mọi `logger.info(...)` bị bỏ.
- **Lỗi thứ hai:** format `'{"msg":"%(message)s"}'` lồng chuỗi `json.dumps(...)` vào trong dấu ngoặc kép, nên dòng log sinh ra không phải JSON hợp lệ.
- **Ở Part 6 tôi sửa cả hai bằng cách:** dùng logger riêng có `JsonFormatter` gọi `json.dumps` cho cả dòng log, và đặt `propagate = False`.

---

## Part 2: Docker

### Exercise 2.1: Dockerfile questions (`02-docker/develop/Dockerfile`)

1. **Base image:** `python:3.11`, bản Debian đầy đủ có sẵn Python, pip và cả compiler (gcc) cùng nhiều thư viện hệ thống. Vì vậy image rất nặng.
2. **Working directory:** `/app`. Các lệnh `COPY`, `RUN` và `CMD` sau đó đều chạy trong thư mục này.
3. **Vì sao `COPY requirements.txt` trước:** Docker cache theo từng layer.
   - Copy `requirements.txt` rồi `pip install` *trước* khi copy code, thì khi chỉ sửa code, layer cài dependencies vẫn dùng lại từ cache. Build chỉ mất vài giây thay vì cài lại toàn bộ thư viện.
   - Nếu `COPY . .` trước, mọi thay đổi code đều làm hỏng cache của bước `pip install`.
4. **CMD vs ENTRYPOINT:**
   - `CMD` là lệnh / tham số mặc định, bị ghi đè khi chạy `docker run image <lệnh khác>`.
   - `ENTRYPOINT` là chương trình cố định của container; tham số của `docker run` được nối thêm vào sau nó, và chỉ đổi được bằng `--entrypoint`.
   - Thường dùng `ENTRYPOINT` cho file thực thi chính và `CMD` cho tham số mặc định.

### Exercise 2.2: Build và run

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -d --name p2-develop -p 8000:8000 my-agent:develop
$ docker ps
NAMES        IMAGE              STATUS         PORTS
p2-develop   my-agent:develop   Up 3 seconds   0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp

$ curl -X POST "http://localhost:8000/ask?question=What%20is%20Docker?"
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}  -> HTTP 200
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":3.0,"container":true}  -> HTTP 200

$ docker exec p2-develop sh -c 'whoami; python --version'
root
Python 3.11.18
```

Image develop chạy bằng **root**. Nếu app bị khai thác lỗ hổng, kẻ tấn công có quyền root trong container.

### Exercise 2.3: Multi-stage build

**Stage 1 (`builder`, `python:3.11-slim`):** cài `gcc`, `libpq-dev`, rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này chỉ để build, không được deploy.

**Stage 2 (`runtime`, `python:3.11-slim`):**
- Tạo user non-root `appuser`, rồi chỉ copy `/root/.local` (các package đã cài) từ builder sang, cùng với `main.py` và `utils/`.
- Chạy bằng `USER appuser`, có `HEALTHCHECK`, `CMD uvicorn ... --workers 2`.

**Vì sao image nhỏ hơn:**
- Image cuối dùng base `slim` thay vì bản đầy đủ.
- `gcc`, header file, cache apt và pip nằm lại ở stage builder và bị bỏ đi, chỉ phần đã cài xong được copy sang.

Kích thước đo bằng `docker images`:

```text
$ docker images my-agent
REPOSITORY   TAG        IMAGE ID       SIZE
my-agent     advanced   36bc6599e970   240MB
my-agent     develop    bb69aa8890ec   1.67GB
```

- Develop: **1670 MB**
- Production (advanced): **240 MB**
- Chênh lệch: (1670 − 240) / 1670 = **85.6 % nhỏ hơn**

(`docker image inspect --format '{{.Size}}'` trên Docker Desktop báo kích thước nén: develop 406.6 MB, advanced 54.7 MB, cũng giảm khoảng 86.5 %.)

Nhận xét: stage builder cài `gcc` và `libpq-dev` nhưng `requirements.txt` (fastapi, uvicorn) đều có wheel sẵn, nên hai gói này chỉ làm build chậm hơn. Image cuối không bị ảnh hưởng.

### Exercise 2.4: Docker Compose stack

Sơ đồ (`02-docker/production/docker-compose.yml`):

```text
                    host :80 (và :443)
                         │
                ┌────────▼────────┐
  Client ──────►│  nginx:alpine   │  reverse proxy, rate limit 10 r/s/IP,
                │  (publish 80)   │  security headers, /health không rate limit
                └────────┬────────┘
                         │ proxy_pass http://agent:8000   (network "internal")
                ┌────────▼────────┐
                │      agent      │  build từ 02-docker/production/Dockerfile (target runtime)
                │  uvicorn :8000  │  user appuser, healthcheck /health
                └───┬─────────┬───┘
     REDIS_URL      │         │      QDRANT_URL
    redis:6379      │         │      qdrant:6333
          ┌─────────▼──┐   ┌──▼──────────────┐
          │ redis:7    │   │ qdrant v1.9.0   │
          │ (volume)   │   │ (volume)        │
          └────────────┘   └─────────────────┘
```

- Chỉ `nginx` publish port ra host.
- `agent`, `redis` và `qdrant` chỉ nằm trong bridge network `internal` và gọi nhau qua DNS tên service.
- `agent` có `depends_on` redis và qdrant với điều kiện `service_healthy`.

```text
$ docker compose -p k5-day14-docker up -d --build
$ docker compose -p k5-day14-docker ps
SERVICE   IMAGE                   STATUS                            PORTS
agent     k5-day14-docker-agent   Up 3 seconds (health: starting)   8000/tcp
nginx     nginx:alpine            Up 2 seconds                      0.0.0.0:80->80/tcp, [::]:80->80/tcp, 0.0.0.0:443->443/tcp, [::]:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 13 seconds (healthy)           6333-6334/tcp
redis     redis:7-alpine          Up 13 seconds (healthy)           6379/tcp

$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block
{"status":"ok","uptime_seconds":1.7,"version":"2.0.0","timestamp":"2026-11-26T08:28:55.201450"}

$ curl -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question":"Explain microservices"}'
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}  -> HTTP 200

$ docker compose -p k5-day14-docker exec agent whoami
appuser
```

Header `Server: nginx` và các security header chứng tỏ request đi qua Nginx rồi mới tới agent.

Nhận xét:
- `main.py` chưa thật sự dùng Redis và Qdrant: có biến `REDIS_URL` và `QDRANT_URL` nhưng code không kết nối. Đây chỉ là khung kiến trúc.
- Compose publish port 443 dù Nginx không cấu hình TLS.

Lúc đầu `docker compose up` báo lỗi `429 Too Many Requests` khi pull `qdrant/qdrant:v1.9.0`, vì Docker Hub giới hạn lượt pull ẩn danh. Tôi pull lại thì được.

---

## Part 3: Cloud Deployment

### Exercise 3.1 / 3.2: Deploy (nền tảng chính: Render)

Nền tảng: Render (free plan). Deploy **chính agent Part 6** (`06-lab-complete/`) bằng Blueprint `06-lab-complete/render.yaml`.

- **URL:** https://production-ai-agent-nrfk.onrender.com
- Chi tiết lệnh test và biến môi trường: [DEPLOYMENT.md](DEPLOYMENT.md)
- Screenshot: [dashboard](screenshots/dashboard.png) · [running](screenshots/running.png) · [test](screenshots/test.png)

```text
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả: `200`, `200`, `401`, `200`.

### Nền tảng còn lại: so sánh `railway.toml` với `render.yaml`

So sánh hai file tôi viết trong `06-lab-complete/` (cùng deploy một Dockerfile):

| Khía cạnh | `railway.toml` | `render.yaml` | Vì sao khác |
|---|---|---|---|
| Phạm vi | Cấu hình **1 service** (build + deploy) | **Blueprint IaC** cho cả hệ thống: web service **và** Key Value (Redis) | Railway tạo Redis bằng dashboard / `railway add`; Render mô tả toàn bộ hạ tầng trong 1 file |
| Định dạng | TOML, bảng `[build]` / `[deploy]` | YAML, danh sách `services:` | Quy ước riêng của mỗi nền tảng |
| Build | `builder = "DOCKERFILE"`, `dockerfilePath` | `runtime: docker`, `dockerfilePath` + `dockerContext` (tính từ gốc repo) + `buildFilter` | Render build từ GitHub repo nên cần biết thư mục con; Railway `railway up` upload thẳng thư mục hiện tại |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout = 100` | `healthCheckPath: /health` | Giống nhau về ý nghĩa: platform chỉ chuyển traffic sang bản mới khi `/health` trả 200 |
| Restart | `restartPolicyType = "ON_FAILURE"`, tối đa 3 lần | Không khai báo, Render tự restart | Railway cho chỉnh policy trong file |
| Biến môi trường / secret | **Không có trong file**: đặt bằng `railway variables`, `REDIS_URL=${{Redis.REDIS_URL}}` | Khai báo trong file: giá trị thường ghi thẳng, secret dùng `sync: false` (nhập tay) hoặc `generateValue: true`, `REDIS_URL` lấy bằng `fromService` | Render là IaC nên có cú pháp tham chiếu giữa các service và đánh dấu secret ngay trong file |
| Plan / region | Không có | `plan: free` | Render chọn plan theo từng service |
| PORT | Railway cấp `PORT`, CMD trong Dockerfile đọc `${PORT:-8000}` | Render cấp `PORT=10000`, cùng cơ chế | Vì vậy cùng một Dockerfile chạy được trên cả hai |

So với file mẫu trong `03-cloud-deployment/`:
- Cả `railway.toml` lẫn `render.yaml` mẫu đều dùng **build không qua Docker**: Railway mẫu ghi `builder = "NIXPACKS"`, Render mẫu ghi `runtime: python`, `buildCommand: pip install`. Lệnh chạy được khai báo bằng `startCommand`.
- Bản của tôi dùng Dockerfile, nên môi trường chạy giống hệt lúc test local và lúc script chấm build image.

### Exercise 3.3: Cloud Run CI/CD (`cloudbuild.yaml` + `service.yaml`)

Pipeline Cloud Build chạy khi push lên `main`, gồm 4 bước tuần tự (`waitFor`):

1. **test:** trong `python:3.11-slim`, cài requirements và chạy `pytest tests/`.
2. **build:** `docker build`, gắn 2 tag `:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tận dụng layer cache.
3. **push:** đẩy cả hai tag lên Container Registry.
4. **deploy:** `gcloud run deploy` image `:$COMMIT_SHA` lên vùng `asia-southeast1`, với `min-instances=1` để tránh cold start, `max-instances=10`, 512Mi / 1 CPU. Secret `OPENAI_API_KEY` lấy từ Secret Manager.

`service.yaml` mô tả lại service đó dưới dạng IaC kiểu Knative:
- Autoscale từ 1 tới 10 instance, mỗi instance nhận tối đa 80 request đồng thời.
- `livenessProbe` gọi `/health`, `startupProbe` gọi `/ready`.
- Secret lấy bằng `secretKeyRef`.

Lưu ý:
- Repo không có thư mục `tests/`, nên bước 1 sẽ fail ngay. Pipeline cần có test thật mới chạy được.
- `service.yaml` dùng tag `:latest` trong khi pipeline deploy theo `$COMMIT_SHA`. Nên thống nhất dùng SHA để rollback chính xác.

---

## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop`)

- **Key được check ở đâu:**
  - Trong dependency `verify_api_key()`. Hàm này đọc header `X-API-Key` qua `APIKeyHeader(name="X-API-Key", auto_error=False)` và được gắn vào endpoint bằng `Depends(verify_api_key)` ở `/ask`.
  - Key đúng lấy từ env `AGENT_API_KEY`.
- **Sai key thì sao:**
  - Thiếu key trả **401** ("Missing API key").
  - Sai key trả **403** ("Invalid API key").
  - Request bị chặn trước khi chạm tới LLM.
- **Rotate key thế nào:**
  - Đổi giá trị env `AGENT_API_KEY` trên platform rồi restart hoặc redeploy. Không phải sửa code.
  - Để không làm gián đoạn client, nên cho phép **2 key cùng lúc** trong thời gian chuyển: key cũ và key mới, ví dụ `AGENT_API_KEYS=old,new`. Client đổi sang key mới, sau đó mới xoá key cũ.
  - Bản hiện tại chỉ nhận 1 key, nên rotate là cắt ngang ngay lập tức.

```text
$ AGENT_API_KEY=secret-key-123 python app.py

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'        # không có key
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

Ở Part 6 tôi trả **401 cho cả thiếu lẫn sai key**, đúng hợp đồng của script chấm, và so sánh bằng `hmac.compare_digest` để tránh timing attack.

### Exercise 4.2: JWT authentication (`04-api-gateway/production`)

**Luồng JWT:**
1. `POST /auth/token` với username / password. `authenticate_user()` kiểm tra với `DEMO_USERS`; sai thì trả 401.
2. `create_token()` ký JWT bằng HS256 với `JWT_SECRET`. Payload gồm `sub` (username), `role`, `iat` và `exp` (60 phút).
3. Client gửi `Authorization: Bearer <token>`. `verify_token()` gọi `jwt.decode()` để kiểm chữ ký và hạn dùng rồi trả về `{username, role}`, **không cần tra database**.

Các trường hợp lỗi:
- Không có token: 401.
- Token hết hạn: 401 ("Token expired").
- Token sai chữ ký hoặc giả: 403.

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
{"access_token":"eyJ***(đã che)***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ***"}

$ curl -i -X POST http://localhost:8000/auth/token -d '{"username":"student","password":"wrong"}'
HTTP/1.1 401 Unauthorized
{"detail":"Invalid credentials"}

$ curl -i -X POST http://localhost:8000/ask -d '{"question":"Explain JWT"}'                         # không có token
HTTP/1.1 401 Unauthorized
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer abc.def.ghi" ...              # token giả
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -d '{"question":"Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Đây là câu trả lời từ AI agent (mock). ...","usage":{"requests_remaining":9,"budget_remaining_usd":2.1e-05}}
```

Payload của token (chỉ là base64, **ai cũng đọc được**, nên không được để secret trong payload):
`{'sub': 'student', 'role': 'user', 'iat': 1795681788, 'exp': 1795685388}`

### Exercise 4.3: Rate limiting

- **Thuật toán:**
  - **Sliding window log**: mỗi user có một `deque` lưu timestamp các request.
  - Mỗi lần check, bỏ các timestamp cũ hơn 60 giây. Nếu số còn lại ≥ limit thì trả 429 kèm `Retry-After` (tính từ timestamp cũ nhất).
  - Docstring ghi "Sliding Window Counter", nhưng code thực chất lưu từng timestamp, tức là sliding window *log*.
- **Limit:** user thường (`student`) 10 request / 60 giây; admin (`teacher`) 100 request / 60 giây.
- **Bypass cho admin:** không có bypass hoàn toàn. `app.py` chọn limiter theo role trong JWT (`rate_limiter_admin if role == "admin" else rate_limiter_user`), nên admin dùng bucket 100 req/phút. Muốn bỏ hẳn giới hạn thì bỏ qua bước `limiter.check()` khi `role == "admin"`.
- **Hạn chế:** state nằm trong RAM của process. Chạy nhiều worker hoặc instance thì mỗi nơi đếm riêng, và restart là mất. Part 6 tôi chuyển state sang Redis (sorted set).

```text
$ for i in {1..20}; do curl -s -o /dev/null -w "%{http_code} " -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\":\"Test $i\"}"; done
200 200 200 200 200 200 200 200 200 429 429 429 429 429 429 429 429 429 429 429
```

Chỉ 9 request đầu trả 200 vì trước vòng lặp đã có 1 request "Explain JWT" (10/10).

```text
$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" ... (request thứ 21)
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795681857
retry-after: 53
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":53}}

$ curl http://localhost:8000/me/usage -H "Authorization: Bearer $TOKEN"
{"user_id":"student","date":"2026-11-26","requests":10,"input_tokens":40,"output_tokens":300,"cost_usd":0.000186,"budget_usd":1.0,"budget_remaining_usd":0.999814,"budget_used_pct":0.0}
```

Phát hiện: trong response của `/ask`, trường `budget_remaining_usd` thực ra chứa **số đã tiêu** (`usage.total_cost_usd`), không phải số còn lại. Endpoint `/me/usage` mới tính đúng.

### Exercise 4.4: Cost guard implementation

**Cách làm:**
- Mỗi user một key Redis `budget:<user_id>:<YYYY-MM>`. Vì tên key chứa tháng, sang tháng mới sẽ dùng key mới, tức là **tự reset đầu tháng**. Key có `EXPIRE` 32 ngày để Redis tự dọn.
- **Check và cộng tiền trong một Lua script**, chạy atomic trên Redis. Nếu tách `GET` rồi `INCRBYFLOAT` như bản gợi ý, 2 request song song (2 worker hoặc 2 instance) có thể cùng đọc thấy "còn budget" rồi cùng vượt.
- Request làm tổng vượt $10 thì trả `False` (endpoint trả 402) và **không bị trừ tiền**.

```python
import os
from datetime import datetime, timezone
import redis

r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
MONTHLY_BUDGET_USD = float(os.getenv("MONTHLY_BUDGET_USD", "10"))

_CHECK_AND_SPEND = r.register_script("""
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local cost = tonumber(ARGV[1])
if current + cost > tonumber(ARGV[2]) then
  return 0
end
redis.call('INCRBYFLOAT', KEYS[1], cost)
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
""")

def check_budget(user_id: str, estimated_cost: float) -> bool:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    key = f"budget:{user_id}:{month}"
    return bool(_CHECK_AND_SPEND(keys=[key], args=[estimated_cost, MONTHLY_BUDGET_USD, 32 * 24 * 3600]))
```

Test trên Redis thật (`redis:7-alpine`):

```text
request 1: cost=$4.0  -> ALLOW  | đã dùng $4.00/$10
request 2: cost=$4.0  -> ALLOW  | đã dùng $8.00/$10
request 3: cost=$4.0  -> BLOCK (402)  | đã dùng $8.00/$10
request 4: cost=$1.5  -> ALLOW  | đã dùng $9.50/$10
request 5: cost=$0.6  -> BLOCK (402)  | đã dùng $9.50/$10
TTL key (giây): 2764800
```

**Trong Part 6** (`06-lab-complete/app/cost_guard.py`):
- `check_budget()` gọi *trước* khi gọi LLM. Nếu tổng chi phí trong ngày ≥ `DAILY_BUDGET_USD` hoặc trong tháng ≥ `MONTHLY_BUDGET_USD` thì trả **402**.
- `record_cost()` gọi *sau* khi gọi LLM, cộng chi phí thật (tính theo token) bằng `INCRBYFLOAT` trong pipeline MULTI/EXEC.
- Script chấm xác nhận: budget 0,000001 USD trả 402 ngay ở request thứ 1.

**Vì sao cần cả rate limit lẫn cost guard:**
- Rate limit chặn **tần suất**, bảo vệ server khỏi bị spam.
- Cost guard chặn **tổng tiền**. 10 request/phút với prompt dài vẫn có thể đốt hết ngân sách sau vài giờ.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks

`05-scaling-reliability/develop/app.py` đã có:
- `/health` (liveness): luôn trả 200 khi process còn sống, kèm uptime, version và mức dùng RAM (`psutil`; trên 90 % thì báo `degraded`).
- `/ready` (readiness): trả 503 khi `_is_ready = False`, tức là đang khởi động hoặc đang shutdown.

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":2.8,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T08:31:13.903462+00:00","checks":{"memory":{"status":"ok","used_percent":6.7}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}
```

**Liveness khác readiness ở đâu:**
- `/health` fail thì platform **restart** container.
- `/ready` fail thì load balancer **ngừng gửi traffic** vào instance đó, nhưng không restart.
- Vì vậy `/health` không nên phụ thuộc Redis: Redis chết mà restart agent cũng không giải quyết được gì, còn gây restart dây chuyền. Readiness thì phải kiểm tra Redis.
- Part 6 làm đúng như vậy: mất Redis thì `/health` vẫn 200, còn `/ready` trả 503.

### Exercise 5.2: Graceful shutdown

Gửi request rồi `kill -TERM` ngay. Chạy trong container Linux, vì Windows không gửi được SIGTERM:

```text
$ curl -X POST "http://localhost:8000/ask?question=Long%20task" &
$ kill -TERM 421
Kết quả request đang chạy:
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}  -> HTTP 200
Process agent thoát với exit code: 0

--- log agent:
INFO:     Shutting down
INFO:     127.0.0.1:57778 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-26 08:31:17,046 INFO 🔄 Graceful shutdown initiated...
2026-11-26 08:31:17,046 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [421]
2026-11-26 08:31:17,046 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

**Request đang chạy vẫn hoàn thành (200).** Thứ tự xử lý:
1. uvicorn nhận SIGTERM, in "Shutting down" và ngừng nhận kết nối mới.
2. uvicorn chờ request đang chạy trả xong.
3. Chạy phần shutdown của `lifespan`.
4. Process thoát với code 0.

Hai lưu ý trong code mẫu:
- **Handler tự đăng ký chạy sau cùng.** Dòng "Received signal 15" của `handle_sigterm` in ra **cuối cùng**. Lý do: uvicorn tạm bắt signal trong lúc chạy server và chỉ gọi lại handler cũ sau khi đã tắt xong. Nếu handler tự viết mà đè mất handler của uvicorn (ví dụ gọi `signal.signal` *sau* khi server đã chạy), process sẽ không bao giờ tắt.
- **`lifespan` dùng `time.sleep()`** trong hàm async, làm block event loop. Nên dùng `await asyncio.sleep()`.

### Exercise 5.3: Stateless design

`05-scaling-reliability/production/app.py` lưu toàn bộ session và conversation history trong Redis: key `session:<id>`, `SETEX` với TTL 1 giờ, giữ tối đa 20 message. Process không giữ state nào trong RAM.

```python
# ❌ Anti-pattern: state trong RAM của process
conversation_history = {}
history = conversation_history.get(user_id, [])

# ✅ Stateless: state trong Redis, instance nào cũng đọc được
data = r.get(f"session:{session_id}")
r.setex(f"session:{session_id}", 3600, json.dumps(session))
```

**Vì sao cần stateless:** khi chạy 3 instance sau load balancer, request 1 của user A có thể vào instance 1 còn request 2 vào instance 2. Nếu history nằm trong RAM của instance 1 thì instance 2 không thấy, và cuộc hội thoại bị mất. Restart hoặc deploy lại cũng làm mất toàn bộ state trong RAM.

Nhận xét: nếu không kết nối được Redis lúc khởi động, app **lặng lẽ chuyển sang dict trong RAM** và vẫn báo healthy. Trong production nên fail `/ready` thay vì âm thầm chạy chế độ không scale được. Ở Part 6, rate limit và cost guard của tôi bắt buộc dùng Redis: mất Redis thì `/ready` trả 503 và `/ask` trả 503 rõ ràng.

### Exercise 5.4: Load balancing

```text
$ cd 05-scaling-reliability/production && touch .env.local
$ docker compose -p k5-day14-scaling up -d --build --scale agent=3
$ docker compose -p k5-day14-scaling ps
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 13 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   Up 13 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   Up 13 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   Up 13 seconds             0.0.0.0:8080->80/tcp, [::]:8080->80/tcp
k5-day14-scaling-redis-1   Up 24 seconds (healthy)   6379/tcp
```

- Nginx resolve tên `agent` qua DNS của Docker (`resolver 127.0.0.11`), được 3 IP, rồi chia request theo round-robin.
- Đếm `POST /chat` trong `docker compose logs agent` (5 request của `test_stateless.py` cộng 2 request thử thêm): agent-1: 2, agent-2: 2, agent-3: 3. Traffic được chia đều.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
Session ID: 0dd10450-856e-4a7f-b377-9ec253e402f5

Request 1: [instance-00230e]   Q: What is Docker?
Request 2: [instance-0a5913]   Q: Why do we need containers?
Request 3: [instance-00230e]   Q: What is Kubernetes?
Request 4: [instance-d2cded]   Q: How does load balancing work?
Request 5: [instance-0a5913]   Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-00230e', 'instance-d2cded', 'instance-0a5913'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
✅ Session history preserved across all instances via Redis!
```

5 request của cùng một session được **3 instance khác nhau** phục vụ, nhưng history vẫn đủ 10 message (5 user + 5 assistant), vì instance nào cũng đọc và ghi cùng một key trong Redis.

**Thử thêm: tắt 1 instance giữa chừng.** Script mặc định không tự tắt instance nào, nên tôi tự làm:

```text
turn 2: HTTP 200 served_by=instance-2ce732 130ms
turn 3: HTTP 200 served_by=instance-09b1fb 158ms
$ docker stop k5-day14-scaling-agent-1
turn 4: HTTP 200 served_by=instance-365395 143ms
turn 5: HTTP 200 served_by=instance-2ce732 136ms
turn 6: HTTP 200 served_by=instance-365395 60196ms   ← chờ 60 s
turn 7: HTTP 200 served_by=instance-2ce732 137ms
turn 8: HTTP 200 served_by=instance-365395 154ms
turn 9: HTTP 200 served_by=instance-2ce732 149ms
turn 10: HTTP 200 served_by=instance-365395 138ms
history count: 20 (mong đợi 20)
```

- **Không mất request nào và không mất state:** cả 10 turn đều trả 200, history đủ 20 message.
- **Nhưng có 1 request mất 60 196 ms.**
  - Nginx đã phân giải `agent` ra 3 IP lúc khởi động. Khi round-robin tới IP của container đã tắt, kết nối bị treo cho tới khi hết `proxy_connect_timeout`, mặc định **60 giây**.
  - Hết thời gian đó, `proxy_next_upstream error timeout` mới chuyển request sang instance khác. Sau đó Nginx đánh dấu upstream hỏng trong `fail_timeout`, mặc định 10 giây.
  - Lần chạy đầu tôi đặt timeout phía client 10 giây, nên request này bị báo timeout luôn.
- **Cách sửa:** trong `nginx.conf` đặt `proxy_connect_timeout 2s;` và `server agent:8000 max_fails=1 fail_timeout=30s;`. Hoặc để Nginx resolve DNS động bằng biến trong `proxy_pass`, khi đó container đã tắt sẽ biến khỏi danh sách.

---

## Part 6: Final Project — output `check_production_ready.py`

Chạy bản đầy đủ (có Docker) trong **WSL Ubuntu**, Python 3.12.16, `os.name == "posix"`:

```text
$ cd 06-lab-complete && python check_production_ready.py
...
✅ 1. Dockerfile (multi-stage, < 500 MB)
     ✅ Docker: build image thành công — 29s
     ✅ Docker: image < 500 MB — 63 MB
     ✅ Docker: container chạy được, GET /health → 200
...
✅ 7. Rate limiting
     ✅ Khi chạy: vượt giới hạn → 429 — 429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)
     ✅ Khi chạy: state nằm trong Redis — restart agent vẫn bị 429
✅ 8. Cost guard
     ✅ Khi chạy: hết budget → 402 — 402 ở request thứ 1
...
✅ 10. Structured logging
     ✅ Khi chạy: mọi dòng log của app là JSON hợp lệ — 17 dòng JSON
✅ 11. Graceful shutdown
     ✅ Code xử lý SIGTERM / lifespan shutdown
     ✅ Khi chạy: SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0
     ✅ Docker: `docker stop` tắt êm (exit code 0, < 10s) — exit code 0, 1.1s
...
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Ghi chú môi trường: Python mặc định của Ubuntu là 3.14, chưa có wheel cho `pydantic-core` 2.9 nên `pip install` phải build bằng Rust và fail. Tôi tạo venv Python 3.12 bằng `uv venv --python 3.12` rồi cài lại.

Các lần chạy trước đó (trước khi có WSL):
- **Windows, chạy đầy đủ:** 11/12 tiêu chí, 42/42 checks đạt.
  - Tiêu chí 11 bị ⚠️ vì Python trên Windows không gửi được SIGTERM.
  - Phần Docker của tiêu chí này vẫn đạt: `docker stop` trả exit code 0 trong 0,7 s.
  - Image 63 MB.
- **Container Linux (`python:3.11-slim`), chạy đầy đủ:** tiêu chí 11 đạt cả phần runtime (request đang chạy vẫn 200, exit code 0) lẫn phần Docker.
- **Public URL:** `python check_production_ready.py --static-only --public-url https://production-ai-agent-nrfk.onrender.com` cho kết quả `✅ Public URL: GET .../health → 200`.

---

## AI log

> Theo [RULES.md](RULES.md): ghi lại mỗi lần dùng AI cho code Part 6 hoặc cho câu trả lời.

| # | Phần | Tôi hỏi AI gì | AI đưa gì | Tôi tự kiểm tra / sửa thế nào |
|---|---|---|---|---|
| 1 | Tổng quan | Đọc đề lab 14 và lên kế hoạch | Tóm tắt yêu cầu, rubric, các lỗi bị trừ điểm, kế hoạch theo điểm | Đối chiếu với RUBRIC.md, SUBMISSION.md, CHECKPOINTS.md |
| 2 | Part 6 | Viết các TODO trong `06-lab-complete/` | Code `config.py`, `auth.py`, `rate_limiter.py`, `cost_guard.py`, `redis_client.py`, `main.py`, Dockerfile, compose, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml` | Chạy `check_production_ready.py` (Windows và container Linux); test tay 401 / 422 / 429 / 503 bằng `docker compose`; đọc lại từng file để giải thích được |
| 3 | Part 3 | Chọn nền tảng và deploy | Hướng dẫn Render Blueprint, sửa đường dẫn trong `render.yaml` | Tự tạo service trên Render, chạy 4 lệnh CP6 (200 / 200 / 401 / 200), tự chụp 3 ảnh |
| 4 | Part 7 Bước 1 | Chạy load test, eval, sửa knowledge base, viết CI | Chạy các lệnh, thêm 2 tài liệu vào `KNOWLEDGE_BASE`, metric `agent_question_length_chars`, `.github/workflows/eval.yml`, điền BENCHMARK_REPORT mục 1–3 | Mọi số trong báo cáo lấy từ file JSON trong `reports/` (đã đối chiếu tự động 34 con số); eval cuối đạt 23/23, 0 regression |
| 6 | Part 7 Bước 2 | Chạy stack observability, benchmark scale, thí nghiệm A–E | Sửa lỗi `PYTHONUSERBASE` trong `production/Dockerfile` (agent crash), chạy Locust headless 1 / 3 instance, đo thời gian alert firing qua API Prometheus, lấy `trace_id` chậm / lỗi từ log, điền BENCHMARK_REPORT mục 4–6 | Tự chụp 4 ảnh Grafana / Jaeger / Prometheus; số bảng scale khớp `benchmark-results/*.csv` (đã đối chiếu); thời gian firing lấy từ output script theo dõi alert |
| 5 | Part 1, 2, 4, 5 | Chạy các ví dụ và soạn câu trả lời | Chạy app / container / compose, ghi output, soạn nháp câu trả lời và các phát hiện (bug logging Part 1, `tests/` thiếu trong Cloud Build, failover 60 s của Nginx) | Đọc lại code mẫu để xác nhận từng phát hiện; số MB và output là từ lệnh thật |