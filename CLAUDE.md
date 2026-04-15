# TeamNoT — Claude Code Project Context

## Tên project
**TeamNoT** (Team No Time) — Autonomous Software Development AI Team

## Mục đích
Hệ thống AI multi-agent tự động phát triển phần mềm, vận hành 24/7 trên MiniPC Windows.
Nhận task qua Telegram → tự phân tích, thiết kế, code, test, review → báo cáo kết quả.

## Stack
- **Gateway:** OpenClaw + Telegram Bot
- **Orchestrator:** MiniMax M2.7 (chạy 24/7, rẻ, nhanh)
- **Architect / Reviewer:** Claude Code CLI subprocess (OAuth, không dùng API key)
- **Implementer / Tester:** Qwen Code CLI
- **Specialist agents (Phase 3):** PM, FE, BE, AI Engineer, DevOps
- **Agent framework:** PraisonAI + Claude Code CLI hybrid
- **Shared knowledge:** Filesystem MCP (local Windows)
- **Python:** 3.12, venv tại `.venv/`
- **Node.js:** v24.14.1

## Đường dẫn quan trọng
- Project root: `C:/Users/Jenky - MiniPC/Desktop/Project/TeamNoT/`
- Projects root: `C:/Users/Jenky - MiniPC/Desktop/Project/`
- Python venv: `.venv/Scripts/python.exe`
- Logs: `LOGS/YYYY-MM-DD.log`
- Reports: `REPORTS/TASK-XXX.md`
- ADRs: `ADRs/ADR-XXX-tên.md`
- OpenClaw config: `%USERPROFILE%\.openclaw\openclaw.json`
- OpenClaw skill: `%USERPROFILE%\.openclaw\workspace\skills\teamnot-orchestrator\SOUL.md`

## Lệnh chạy
```powershell
# Activate venv
.venv\Scripts\Activate.ps1

# Chạy task trực tiếp
python teamnot.py "Tạo FastAPI CRUD API cho module Product"

# Test syntax
python -c "import ast; ast.parse(open('teamnot.py', encoding='utf-8').read()); print('OK')"

# Restart OpenClaw sau khi thay đổi config
openclaw gateway restart
openclaw doctor --fix
```

## Quy tắc quan trọng nhất
1. **KHÔNG commit lên `main`** — mỗi task làm trong branch `feature/TASK-XXX-description`
2. **KHÔNG hardcode secrets** — tất cả trong `.env` (file này đã có trong `.gitignore`)
3. **KHÔNG deploy production** mà không có lệnh tường minh từ user
4. **Chạy linter trước khi report done** — `ruff check . --fix` cho Python
5. **Đọc `AGENT_MEMORY.md` trước khi bắt đầu task mới** — đây là nguồn conventions
6. **Retry logic:** Reviewer REJECT → Implementer retry tối đa 3 lần → escalate
7. **Cost limit:** $5 USD/task — báo user nếu ước tính vượt

## Files cốt lõi
| File | Mục đích |
|---|---|
| `teamnot.py` | Main orchestration script — entry point |
| `claude_worker.py` | Claude Code CLI wrapper (Phase 3) |
| `cli.py` | Terminal CLI: run, queue, project, sprint, test-claude |
| `task_queue.py` | Parallel task queue với persistence |
| `cost_tracker.py` | Token usage và cost tracking |
| `PROJECT_CONTEXT.md` | Tri thức cốt lõi, quy tắc, agent roles |
| `TASK_BOARD.md` | Trạng thái task real-time (Orchestrator cập nhật) |
| `AGENT_MEMORY.md` | Patterns, conventions tích lũy qua các task |
| `SPRINTS/SPRINT_CURRENT.md` | Sprint board cho Phase 3 project mode |
| `PROJECT_DOCS/` | API contracts, FE/AI requests, QA reports |
| `.env` | API keys và config (KHÔNG commit) |
| `.claude/agents/` | Sub-agent definitions cho Claude Code |

## Agents trong `.claude/agents/`
- `architect.md` — Thiết kế solution, tạo ADR, không viết code
- `implementer.md` — Viết code theo ADR, chạy linter, không commit main
- `reviewer.md` — Review checklist security/quality/convention, output APPROVE/REJECT
