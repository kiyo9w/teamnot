# TeamNoT — Status Snapshot
> Tài liệu này mô tả **trạng thái đầy đủ** của hệ thống TeamNoT tính đến 2026-04-15.
> Dùng để bàn giao ngữ cảnh cho AI hoặc thành viên mới.
> **Đọc file này là đủ để hiểu toàn bộ hệ thống.**

---

## 1. TeamNoT là gì?

**TeamNoT** (Team No Time) là hệ thống AI multi-agent **tự động phát triển phần mềm**, vận hành 24/7 trên MiniPC Windows cục bộ. Người dùng giao task bằng ngôn ngữ tự nhiên qua Telegram, hệ thống tự:
1. Phân tích yêu cầu
2. Thiết kế kiến trúc (ADR)
3. Viết code
4. Viết và chạy test
5. Review code
6. Cập nhật tài liệu
7. Báo cáo kết quả qua Telegram

**Mission:** Từ một câu mô tả → sản phẩm chạy được, có test, có tài liệu.

---

## 2. Trạng thái triển khai

| Phase | Tên | Trạng thái | Mô tả |
|---|---|---|---|
| Phase 0 | Thiết kế kiến trúc | ✅ DONE | Knowledge base đầy đủ |
| Phase 1 | PraisonAI MVP | ✅ DONE | 4 agents cơ bản, pipeline chạy được |
| Phase 2 | Full team + Queue | ✅ DONE | 7 agents, parallel queue, cost tracking, CLI |
| Phase 3 | CrewAI parallel | ⏳ PENDING | So sánh PraisonAI vs CrewAI |
| Phase 4 | Optimize | ⏳ PENDING | RAG, GitHub integration, dashboard |

**Commit mới nhất:** `7713d31 feat: Phase 2 — 7-agent team, parallel queue, cost tracking`

---

## 3. Môi trường máy chủ

| Thành phần | Giá trị |
|---|---|
| Máy | Windows 11 MiniPC (user: Jenky - MiniPC) |
| Python | 3.12, venv tại `.venv/` |
| Node.js | v24.14.1 |
| Project root | `C:/Users/Jenky - MiniPC/Desktop/Project/TeamNoT/` |
| Projects root | `C:/Users/Jenky - MiniPC/Desktop/Project/` |
| OpenClaw config | `%USERPROFILE%\.openclaw\openclaw.json` |
| OpenClaw skill | `%USERPROFILE%\.openclaw\workspace\skills\teamnot-orchestrator\SOUL.md` |

---

## 4. Cấu trúc thư mục đầy đủ

```
TeamNoT/
├── .claude/
│   └── agents/
│       ├── architect.md      ← Sub-agent definition cho Claude Code
│       ├── implementer.md    ← Sub-agent definition cho Claude Code
│       └── reviewer.md       ← Sub-agent definition cho Claude Code
├── .venv/                    ← Python virtual environment (không commit)
├── ADRs/                     ← Architecture Decision Records (được tạo khi chạy task)
├── LOGS/                     ← Log files (không commit)
│   └── cost_tracking.json    ← Chi phí API theo task
├── REPORTS/                  ← Final reports sau mỗi task (được tạo khi chạy task)
├── .env                      ← API keys và config (KHÔNG commit)
├── .gitignore
├── AGENT_MEMORY.md           ← Conventions và lessons tích lũy — agents ĐỌC TRƯỚC
├── CHANGELOG.md              ← (sẽ được Documenter tạo)
├── CLAUDE.md                 ← Context cho Claude Code CLI
├── CREWAI_PLAN.md            ← Kế hoạch Phase 3
├── IMPLEMENTATION_GUIDE.md   ← Hướng dẫn deploy kỹ thuật
├── PROJECT_CONTEXT.md        ← Tri thức cốt lõi — MỌI agent đọc file này
├── README.md                 ← Overview
├── STATUS_SNAPSHOT.md        ← File này
├── TASK_BOARD.md             ← Trạng thái task real-time
├── WORKFLOW_V2.md            ← Kiến trúc workflow mới nhất
├── cli.py                    ← ⭐ CLI interface Phase 2
├── cost_tracker.py           ← ⭐ Cost tracking Phase 2
├── task_queue.py             ← ⭐ Parallel task queue Phase 2
├── task_queue.json           ← Queue persistent state
└── teamnot.py                ← ⭐ Main orchestration script
```

---

## 5. Stack kỹ thuật

### Runtime TeamNoT
| Component | Tool | Model/Version | Vai trò |
|---|---|---|---|
| Gateway & Telegram | OpenClaw | — | Nhận/gửi message Telegram |
| Orchestrator | PraisonAI Agent | MiniMax M2.7 | Điều phối, decompose, track |
| Architect | PraisonAI Agent | Claude Sonnet 4.6 | Thiết kế, ADR |
| Implementer | PraisonAI Agent | Qwen Coder Plus | Viết code |
| Tester | PraisonAI Agent | Qwen Coder Plus | Viết và chạy pytest |
| Reviewer | PraisonAI Agent | Claude Sonnet 4.6 | Review, APPROVE/REJECT |
| Researcher | PraisonAI Agent | MiniMax M2.7-highspeed | Research thư viện |
| Documenter | PraisonAI Agent | MiniMax M2.7-highspeed | Cập nhật README/CHANGELOG |
| Shared knowledge | Filesystem MCP | npx @modelcontextprotocol/server-filesystem | Đọc/ghi file local |

### Stack phát triển sản phẩm (output của TeamNoT)
- **Backend:** FastAPI (Python async), Express/Fastify (Node.js)
- **Frontend:** Next.js 14+ (App Router), Vue 3, Flutter
- **Database:** PostgreSQL + SQLAlchemy 2.0 (async), Redis, SQLite
- **AI features:** LangChain/LlamaIndex (RAG), YOLOv8 (CV)
- **Deploy:** Docker Compose, Nginx, GitHub Actions

---

## 6. Agent roster — 7 agents Phase 2

### OrchestratorX
- **Model:** `openai/minimax/MiniMax-M2.7`
- **Vai trò:** Nhận task → decompose → assign → track pipeline → report
- **Context inject:** Đọc PROJECT_CONTEXT.md + TASK_BOARD.md + AGENT_MEMORY.md khi khởi tạo
- **Loop:** `agent.run_until()` với `threshold=8.5`, `max_iterations=40`
- **Quyền:** Đọc/ghi mọi shared file qua Filesystem MCP
- **Giới hạn:** KHÔNG tự viết code, KHÔNG deploy production

### Architect
- **Model:** `anthropic/claude-sonnet-4-6`
- **Vai trò:** Thiết kế solution, tạo ADR tại `ADRs/TASK-ID.md`
- **Output format:** Architecture Decision Record đầy đủ (Context, Decision, Alternatives, Consequences, Implementation notes, File structure, Dependencies, API contracts)
- **Giới hạn:** KHÔNG viết implementation code

### Implementer
- **Model:** `openai/qwen-coder-plus`
- **Vai trò:** Viết code theo ADR đã approve, tạo git branch `feature/TASK-ID`
- **Luôn làm:** Đọc AGENT_MEMORY.md trước → chạy `ruff check . --fix` sau
- **Giới hạn:** KHÔNG commit main, KHÔNG deploy, KHÔNG skip linter

### Tester
- **Model:** `os.getenv("QWEN_MODEL", "openai/qwen-coder-plus")`
- **Vai trò:** Viết pytest, chạy `pytest --cov=app --cov-report=term-missing -v`
- **Target:** 60% coverage minimum
- **Giới hạn:** KHÔNG sửa production code

### Reviewer
- **Model:** `anthropic/claude-sonnet-4-6`
- **Vai trò:** Review theo checklist Security/Quality/Conventions/Edge cases
- **Output:** Chỉ `APPROVE` hoặc `REJECT` — không có trạng thái trung gian
- **Giới hạn:** KHÔNG sửa code trực tiếp, KHÔNG approve khi có security issue

### Researcher
- **Model:** `openai/minimax/MiniMax-M2.7-highspeed`
- **Gọi khi:** Task dùng thư viện chưa có trong AGENT_MEMORY.md
- **Output:** Summary + Recommendation ngắn gọn (≤400 words)
- **Giới hạn:** Không implement code

### Documenter
- **Model:** `openai/minimax/MiniMax-M2.7-highspeed`
- **Luôn cập nhật sau mỗi task:** README.md, CHANGELOG.md, AGENT_MEMORY.md
- **Giới hạn:** Không chạm production code

---

## 7. Pipeline thực thi (thứ tự bắt buộc)

```
User nhắn Telegram
        │
        ▼
OpenClaw → SOUL.md skill → cli.py hoặc teamnot.py
        │
        ▼
OrchestratorX.run_until()
        │
        ├─ 1. Researcher    (nếu cần — thư viện chưa biết)
        │        │
        ├─ 2. Architect ────▶ ADRs/TASK-ID.md
        │        │
        ├─ 3. Implementer ──▶ feature/TASK-ID branch + linter
        │        │
        ├─ 4. Tester ───────▶ tests/ + pytest --cov
        │        │
        ├─ 5. Reviewer ─────▶ APPROVE / REJECT
        │        │
        │        └─ REJECT → Implementer retry (max 3 lần)
        │                    → Sau 3 fail: escalate → approach khác
        │                    → Sau 2 approach: BLOCKED → báo user
        │
        ├─ 6. Documenter ───▶ README + CHANGELOG + AGENT_MEMORY update
        │
        └─ 7. OrchestratorX → REPORTS/TASK-ID.md → Telegram
```

---

## 8. Multi-task queue (Phase 2)

### task_queue.py — cách hoạt động
- **Persistent:** Lưu trạng thái vào `task_queue.json`, crash-safe
- **Crash recovery:** RUNNING → QUEUED tự động khi khởi động lại
- **Thread isolation:** Mỗi task chạy trong `threading.Thread(daemon=True)`
- **Max parallel:** 3 task đồng thời (env `TEAMNOT_MAX_PARALLEL`)
- **Priority sort:** 1 = cao nhất, 10 = thấp nhất

### Task lifecycle
```
QUEUED → RUNNING → DONE
                 → FAILED  (exception trong runner)
       → PAUSED  (user yêu cầu)
       → BLOCKED (escalate sau retry)
```

### Task ID format
`TASK-YYYYMMDD-HHMMSS` — ví dụ: `TASK-20260415-143022`

---

## 9. CLI commands (cli.py)

```powershell
# Activate venv trước
.venv\Scripts\Activate.ps1

# Chạy ngay 1 task (blocking)
python cli.py run "Tạo FastAPI CRUD cho module Product"

# Thêm nhiều task vào queue (không chạy ngay)
python cli.py queue "Task A" "Task B" "Task C"

# Thêm task ưu tiên cao
python cli.py queue-p "Task khẩn cấp" --priority 1

# Chạy queue loop song song (blocking, Ctrl+C để dừng)
python cli.py loop

# Xem trạng thái tất cả task
python cli.py status

# Xem chi phí API
python cli.py cost

# Tạm dừng task (cần xác nhận "xác nhận tạm dừng TASK-ID" từ user trước)
python cli.py pause TASK-20260415-143022

# Tiếp tục task đã pause
python cli.py resume TASK-20260415-143022
```

---

## 10. Environment variables (.env)

```env
# Telegram
TELEGRAM_BOT_TOKEN=<token thực>

# AI Models
MINIMAX_API_KEY=<key thực — đã điền>
ANTHROPIC_API_KEY=<cần điền>

# Qwen
QWEN_MODEL=qwen-coder-plus
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# TeamNoT paths
TEAMNOT_ROOT=C:/Users/Jenky - MiniPC/Desktop/Project/TeamNoT
TEAMNOT_PROJECTS_ROOT=C:/Users/Jenky - MiniPC/Desktop/Project

# Limits
TEAMNOT_COST_LIMIT=5.0
TEAMNOT_MAX_PARALLEL=3
```

**Trạng thái keys:**
- `MINIMAX_API_KEY`: ✅ Đã điền
- `ANTHROPIC_API_KEY`: ⚠️ Chưa điền (cần để dùng Architect/Reviewer)
- `TELEGRAM_BOT_TOKEN`: ⚠️ Chưa điền (cần để nhận task qua Telegram)

---

## 11. Cost model

| Model | Input ($/1M tokens) | Output ($/1M tokens) |
|---|---|---|
| MiniMax M2.7 | $0.30 | $1.20 |
| MiniMax M2.7-highspeed | $0.60 | $2.40 |
| Claude Sonnet 4.6 | $3.00 | $15.00 |
| Qwen Coder Plus | $0.35 | $1.40 |

**Limit:** $5 USD/task — cảnh báo trước khi bắt đầu nếu ước tính vượt.
**Tracking:** `LOGS/cost_tracking.json` (không commit, trong .gitignore).

---

## 12. Shared knowledge files — ai đọc gì

| File | Ai đọc | Mục đích |
|---|---|---|
| `PROJECT_CONTEXT.md` | **MỌI agent** — bắt buộc | Quy tắc vận hành, agent roles, output formats |
| `AGENT_MEMORY.md` | **MỌI agent** — bắt buộc | Conventions, patterns, library decisions đã được approve |
| `TASK_BOARD.md` | Orchestrator, user | Trạng thái task real-time |
| `ADRs/` | Implementer, Tester, Reviewer | Design decisions cho từng task |
| `REPORTS/` | User, Orchestrator | Final report sau mỗi task |
| `LOGS/` | Developer, monitoring | Log execution và cost |

---

## 13. Quy tắc vận hành — BẮT BUỘC

1. **Làm đến khi xong** — Orchestrator không `end_turn` đến khi task done hoặc BLOCKED
2. **Không hỏi lại user** trong lúc làm — tự research hoặc ghi assumption vào AGENT_MEMORY
3. **Interrupt handling:**
   - Hỏi tiến độ → trả lời % từ TASK_BOARD, tiếp tục
   - Task mới → spawn subagent riêng, main pipeline tiếp tục
   - Muốn dừng → hỏi xác nhận: `"xác nhận tạm dừng [TASK-ID]"` mới dừng
4. **Retry:** Reviewer REJECT → Implementer retry (max 3 lần) → escalate → BLOCKED
5. **Security:** KHÔNG deploy production, KHÔNG commit main, KHÔNG hardcode secrets
6. **Cost:** Báo user nếu ước tính > $5 trước khi bắt đầu

---

## 14. Output formats chuẩn

### ADR (Architecture Decision Record)
Lưu tại `ADRs/ADR-XXX-tên.md`:
```markdown
# ADR-XXX — [Tên]
Date: YYYY-MM-DD | Status: Proposed/Accepted/Deprecated
## Context | ## Decision | ## Alternatives | ## Consequences | ## Implementation notes
```

### Code Review (Reviewer output)
```markdown
## Review: [Task ID] — [tên]
### Verdict: APPROVE / REJECT
### Security / Code quality / Conventions / Edge cases (checklist)
### Issues (nếu REJECT): file:line + hướng fix
```

### Final Report (gửi Telegram)
```markdown
## TeamNoT — Báo cáo hoàn thành
**Task / Thời gian / Status:** DONE / DONE_WITH_WARNINGS / BLOCKED
### Đã làm / Kết quả / Warnings / Blockers / Assumptions / Bước tiếp theo
```

---

## 15. Workflow từ góc nhìn người dùng (WORKFLOW_V2.md)

```
[User] → Claude chat (planning) → Mimi (MiniMax) điều phối
                                        │
                            PraisonAI agents thực thi
                            (Qwen Code + MiniMax workers)
                                        │
                            Claude Code CLI review (terminal)
                                        │
                            APPROVE → merge / REJECT → fix loop
```

**Stop condition:** Claude Code review và báo "✅ HOÀN THÀNH"

---

## 16. Những gì CHƯA triển khai (Phase 3+)

| Tính năng | Phase | Ghi chú |
|---|---|---|
| CrewAI parallel benchmark | 3 | So sánh PraisonAI vs CrewAI |
| Context pruning | 2 | Tránh context overflow trong long-running tasks |
| GitHub integration (auto PR) | 4 | Tạo PR tự động sau khi Reviewer APPROVE |
| Dashboard Telegram inline keyboard | 4 | Monitor trực quan qua bot |
| RAG cho codebase knowledge | 4 | Agent tìm kiếm code hiệu quả hơn |
| Model routing tự động | 4 | Task đơn giản → model rẻ, task phức tạp → model mạnh |
| Multi-task queue (stable test) | 2 | Queue đã implement nhưng chưa test thực tế với AI models |

---

## 17. Vấn đề đã biết / cần lưu ý

| Vấn đề | Trạng thái | Ghi chú |
|---|---|---|
| `ANTHROPIC_API_KEY` chưa điền | ⚠️ Cần fix | Architect + Reviewer không chạy được |
| `TELEGRAM_BOT_TOKEN` chưa điền | ⚠️ Cần fix | Không nhận task qua Telegram |
| OpenClaw chưa restart sau Phase 2 | ⚠️ Cần fix | SOUL.md mới chưa được load |
| Queue chưa test với AI models thực | ⚠️ Pending | Đã test logic, chưa test full pipeline |
| Windows console encoding | ✅ Đã fix | cli.py dùng `PYTHONIOENCODING=utf-8` |
| PowerShell execution policy | ✅ Đã fix | `RemoteSigned` scope CurrentUser |
| MCP `--help` error | ✅ OK | Behavior bình thường — server hoạt động đúng |

---

## 18. Lệnh để bắt đầu ngay

```powershell
cd "C:\Users\Jenky - MiniPC\Desktop\Project\TeamNoT"
.venv\Scripts\Activate.ps1

# Test 1 task nhỏ (sau khi điền ANTHROPIC_API_KEY)
python cli.py run "Create health_check.py returning status and timestamp dict"

# Xem queue status
python cli.py status

# Xem cost
python cli.py cost
```

---

## 19. File quan trọng nhất để đọc (theo thứ tự)

1. `PROJECT_CONTEXT.md` — quy tắc vận hành đầy đủ, agent roles, output formats
2. `AGENT_MEMORY.md` — conventions Python/JS/DB/Git đã được approve
3. `teamnot.py` — toàn bộ agent definitions và pipeline logic
4. `task_queue.py` — parallel queue implementation
5. `cli.py` — interface để tương tác với hệ thống
6. `WORKFLOW_V2.md` — kiến trúc workflow từ góc nhìn user
