# Deployment Information

## Public URL

https://production-ai-agent-nrfk.onrender.com

## Platform

**Render** (Blueprint, free plan), deploy từ `06-lab-complete/render.yaml` trên nhánh `main`.

| Thành phần | Loại trên Render | Ghi chú |
|---|---|---|
| `production-ai-agent` | Web Service, runtime Docker | Build từ `06-lab-complete/Dockerfile` (multi-stage), health check `GET /health` |
| `agent-redis` | Key Value (Redis) | Chỉ cho kết nối nội bộ (`ipAllowList: []`), agent nhận `REDIS_URL` qua `fromService` |

`railway.toml` cũng có sẵn trong `06-lab-complete/` nếu cần deploy lên Railway.

## Test Commands

Đặt biến trước khi chạy (key không ghi trong repo):

```bash
BASE=https://production-ai-agent-nrfk.onrender.com
AGENT_API_KEY=YOUR_KEY
```

### Self-test CP6 (chỉ in mã HTTP)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả chạy ngày 2026-11-26:

```text
200
200
401
200
```

### Health check

```bash
curl "$BASE/health"
# {"status":"ok"}

curl "$BASE/ready"
# {"status":"ready","redis":"ok"}
```

### API test (with authentication)

```bash
curl -X POST "$BASE/ask" \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{...}}
```

### Rate limiting (10 req/phút / API key)

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -w '%{http_code} ' -X POST "$BASE/ask" \
    -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"test"}'
done; echo
```

Kết quả (2 request đã gửi trước đó trong cùng phút):

```text
200 200 200 200 200 200 200 200 429 429 429 429
```

### Script chấm

```bash
cd 06-lab-complete
python check_production_ready.py --static-only --public-url https://production-ai-agent-nrfk.onrender.com
# ✅ Public URL: GET https://production-ai-agent-nrfk.onrender.com/health → 200
```

## Environment Variables Set

Đặt trên Render (Blueprint / Dashboard → Environment), **không** ghi trong code:

| Biến | Giá trị / nguồn |
|---|---|
| `PORT` | Render tự cấp; `CMD` trong Dockerfile đọc `${PORT:-8000}` |
| `AGENT_API_KEY` | Secret, `sync: false` trong `render.yaml`, nhập tay trên dashboard |
| `REDIS_URL` | `fromService` → connection string của Key Value `agent-redis` |
| `ENVIRONMENT` | `production` |
| `LOG_LEVEL` | `INFO` |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | `10.0` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
