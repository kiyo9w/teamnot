# AGENT_MEMORY.md — TeamNoT
> Đọc file này TRƯỚC KHI bắt đầu bất kỳ task nào.
> File được Orchestrator X cập nhật sau mỗi task hoàn thành.
> Last updated: 2026-04-14 (initial seed)

---

## Code conventions

### Python
- Framework: FastAPI (async) cho REST API, không dùng Flask
- Package manager: pip + requirements.txt (hoặc pyproject.toml nếu project lớn)
- Linter: ruff (`ruff check . --fix` trước khi commit)
- Type hints: BẮT BUỘC cho tất cả function signatures
- Async: dùng `async def` cho tất cả route handlers và I/O operations
- Error handling: raise HTTPException với status code và detail cụ thể
- Logging: dùng `logging` standard library, không dùng print()
- Config: dùng `pydantic-settings` với `.env` file

### JavaScript / TypeScript
- Framework: Next.js 14+ (App Router) cho web, Fastify cho backend API
- Package manager: pnpm (không dùng npm/yarn)
- Linter: ESLint + Prettier (`pnpm lint` trước khi commit)
- TypeScript: strict mode ON, không dùng `any`
- State: Zustand (lightweight) hoặc React Query (server state)
- Styling: Tailwind CSS

### Database
- Primary: PostgreSQL với SQLAlchemy 2.0 (async) cho Python
- ORM: SQLAlchemy cho Python, Prisma cho TypeScript
- Migrations: Alembic cho Python, Prisma Migrate cho TypeScript
- KHÔNG raw SQL trừ khi có lý do rõ ràng

### Git conventions
- Branch: `feature/TASK-XXX-short-description`
- Commit message: `feat: mô tả ngắn` / `fix: mô tả` / `chore: mô tả`
- Không commit trực tiếp lên `main`

---

## Known gotchas

### Windows MiniPC specific
- Path separator: dùng `pathlib.Path` thay vì hardcode `/` hay `\`
- PowerShell execution policy: có thể cần `Set-ExecutionPolicy RemoteSigned`
- Port conflicts: kiểm tra port 8000 (FastAPI), 3000 (Next.js) trước khi start
- Python venv: luôn dùng venv, không cài global packages

### MiniMax API
- Base URL: `https://api.minimax.io/anthropic`
- Auth header: Bearer token
- Streaming: disabled thinking by default trên Anthropic-compatible path
- Rate limit: kiểm tra coding plan usage nếu dùng coding plan key

### PraisonAI
- Cài: `pip install "praisonai[claw]"` cho Telegram integration
- Telegram bot token: env var `TELEGRAM_BOT_TOKEN`
- `run_until` threshold: đặt 8.5–9.0 để tránh false positive
- Memory scope: dùng `project` scope để agents share memory

### OpenClaw
- Config file: `%USERPROFILE%\.openclaw\openclaw.json`
- Restart gateway sau khi thay đổi config: `openclaw gateway restart`
- MCP filesystem: chạy qua `npx -y @modelcontextprotocol/server-filesystem`
- exec tool: dùng PowerShell làm shell trên Windows

---

## Library decisions

| Use case | Chosen library | Reason |
|---|---|---|
| REST API Python | FastAPI | Async, auto docs, type safe |
| ORM Python | SQLAlchemy 2.0 | Mature, async support |
| Validation Python | Pydantic v2 | FastAPI native, fast |
| HTTP client Python | httpx | Async support, similar API to requests |
| Testing Python | pytest + pytest-asyncio | De facto standard |
| PDF generation | reportlab | Sync — dùng trong ThreadPoolExecutor |
| Task queue | Celery + Redis | Khi cần background tasks |
| WebSocket | FastAPI WebSocket | Built-in |
| ORM TypeScript | Prisma | Best DX, type-safe |
| HTTP client TypeScript | ky | Lightweight, promise-based |
| Testing TypeScript | Vitest | Fast, ESM native |

---

## Architecture patterns

### FastAPI project structure
```
project/
├── app/
│   ├── main.py          # FastAPI app instance, lifespan
│   ├── config.py        # pydantic-settings
│   ├── database.py      # SQLAlchemy engine, session
│   ├── models/          # SQLAlchemy models
│   ├── schemas/         # Pydantic schemas (request/response)
│   ├── routers/         # APIRouter per feature
│   ├── services/        # Business logic
│   ├── repositories/    # DB access layer
│   └── dependencies/    # FastAPI dependencies (auth, db session)
├── tests/
│   ├── conftest.py
│   ├── unit/
│   └── integration/
├── alembic/
├── .env
├── requirements.txt
└── README.md
```

### API response format
```python
# Success
{"success": True, "data": {...}, "message": "OK"}
# Error
{"success": False, "error": "ERROR_CODE", "message": "Mô tả lỗi"}
```

### Auth pattern
- JWT access token (15 min) + refresh token (7 days)
- FastAPI dependency injection cho auth
- `get_current_user` dependency inject vào protected routes

---

## Recurring issues log

| Date | Issue | Root cause | Fix |
|---|---|---|---|
| (sẽ được cập nhật qua thời gian) | | | |

---

## Performance benchmarks

| Metric | Baseline | Target |
|---|---|---|
| Task completion time (simple CRUD) | — | < 20 min |
| Task completion time (full feature) | — | < 60 min |
| Code review pass rate (first attempt) | — | > 70% |
| Test coverage | — | > 60% |
| API cost per task | — | < $1.50 |

---

## Phase 2 — cập nhật 2026-04-15

### Agents mới (đã thêm vào teamnot.py)
- **Researcher**: web search trước khi Architect design — model: MiniMax M2.7-highspeed
- **Documenter**: README, CHANGELOG, AGENT_MEMORY sau mỗi task — model: MiniMax M2.7-highspeed
- **Tester** (cập nhật): dùng QWEN_MODEL từ .env, chạy pytest --cov

### Pipeline Phase 2 (thứ tự nghiêm ngặt)
```
Researcher (nếu cần) → Architect → Implementer → Tester → Reviewer → Documenter
```

### Multi-task queue (task_queue.py)
- Tối đa 3 task chạy song song (TEAMNOT_MAX_PARALLEL=3 trong .env)
- Mỗi task trong thread riêng, không block nhau
- Queue persistent: lưu task_queue.json, crash-safe (RUNNING → QUEUED on restart)
- Priority: 1=cao nhất, 10=thấp nhất

### Cost tracking (cost_tracker.py)
- Ghi vào LOGS/cost_tracking.json
- Cảnh báo khi task vượt $5 USD
- Hàm: log_usage(), task_total(), summary(), check_limit()

### CLI commands (cli.py)
```
python cli.py run "task"                    # chạy ngay 1 task
python cli.py queue "t1" "t2"               # thêm nhiều task vào queue
python cli.py queue-p "task" --priority 1   # task ưu tiên cao
python cli.py loop                          # chạy queue loop song song (blocking)
python cli.py status                        # xem trạng thái tất cả task
python cli.py cost                          # xem chi phí tổng
python cli.py pause TASK-ID                 # tạm dừng (cần xác nhận trước)
python cli.py resume TASK-ID                # tiếp tục task đã pause
```

### Cost model (USD/1M tokens)
| Model | Input | Output |
|---|---|---|
| MiniMax M2.7 | $0.30 | $1.20 |
| MiniMax M2.7-highspeed | $0.60 | $2.40 |
| Claude Sonnet 4.6 | $3.00 | $15.00 |
| Qwen Coder Plus | $0.35 | $1.40 |

### Known gotchas Phase 2
- `run_task()` chấp nhận cả `str` và `QueuedTask` object
- `run_queue_loop()` là blocking — dùng trong background process
- ThreadPoolExecutor không dùng ở đây — dùng threading.Thread trực tiếp để có daemon=True
- TASK-ID format: `TASK-YYYYMMDD-HHMMSS`
