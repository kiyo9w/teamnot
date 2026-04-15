# IMPLEMENTATION_GUIDE.md — TeamNoT Phase 1
> Hướng dẫn triển khai kỹ thuật đầy đủ cho Phase 1 (PraisonAI MVP).
> Chạy từng bước theo thứ tự. Không skip bước nào.

---

## Bước 1 — Chuẩn bị môi trường (MiniPC Windows)

```powershell
# Tạo project folder
mkdir C:\Projects\TeamNoT
cd C:\Projects\TeamNoT

# Tạo Python virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# Cài PraisonAI với Telegram support
pip install "praisonai[claw]"
pip install python-dotenv httpx

# Kiểm tra Node.js cho MCP
node --version  # cần >= 18

# Cài MCP filesystem server
npx -y @modelcontextprotocol/server-filesystem --help
```

---

## Bước 2 — Cấu hình .env

Tạo file `C:\Projects\TeamNoT\.env`:
```env
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token_here

# AI Models
MINIMAX_API_KEY=your_minimax_key
ANTHROPIC_API_KEY=your_claude_key
OPENAI_API_KEY=your_openai_key  # nếu dùng

# OpenClaw
OPENCLAW_GATEWAY_TOKEN=your_gateway_token

# TeamNoT paths
TEAMNOT_ROOT=C:/Projects/TeamNoT
TEAMNOT_PROJECTS_ROOT=C:/Projects

# Cost limit per task (USD)
TEAMNOT_COST_LIMIT=5.0
```

---

## Bước 3 — Main orchestration script

Tạo `C:\Projects\TeamNoT\teamnot.py`:

```python
"""
TeamNoT — Autonomous Software Development AI Team
Chạy: python teamnot.py
"""
import os
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from praisonaiagents import Agent, Agents, MCP

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(f"LOGS/{datetime.now().strftime('%Y-%m-%d')}.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("TeamNoT")

ROOT = Path(os.getenv("TEAMNOT_ROOT", "C:/Projects/TeamNoT"))
PROJECTS_ROOT = Path(os.getenv("TEAMNOT_PROJECTS_ROOT", "C:/Projects"))

# ── Shared MCP tools ───────────────────────────────────────────────
filesystem_mcp = MCP(
    command="npx",
    args=[
        "-y", "@modelcontextprotocol/server-filesystem",
        str(ROOT),
        str(PROJECTS_ROOT),
    ]
)

# ── Shared context loader ──────────────────────────────────────────
def load_shared_context() -> str:
    files = ["PROJECT_CONTEXT.md", "TASK_BOARD.md", "AGENT_MEMORY.md"]
    content = []
    for f in files:
        path = ROOT / f
        if path.exists():
            content.append(f"=== {f} ===\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(content)

# ── Agent definitions ──────────────────────────────────────────────
def build_orchestrator() -> Agent:
    ctx = load_shared_context()
    return Agent(
        name="OrchestratorX",
        role="Engineering Manager & Orchestrator",
        llm="openai/minimax/MiniMax-M2.7",
        tools=[filesystem_mcp],
        memory=True,
        instructions=f"""
Bạn là Orchestrator X — Tech Lead AI của hệ thống TeamNoT.

=== SHARED CONTEXT ===
{ctx}

=== QUY TẮC VẬN HÀNH ===

1. WORKFLOW CHÍNH (không kết thúc cho đến khi task done):
   a. Đọc TASK_BOARD.md → xác định task cần làm
   b. Đọc PROJECT_CONTEXT.md → hiểu constraints
   c. Đọc AGENT_MEMORY.md → áp dụng patterns đã biết
   d. Decompose task thành subtasks có thứ tự
   e. Assign cho đúng agent (xem Agent Roster trong PROJECT_CONTEXT.md)
   f. Theo thứ tự: Researcher → Architect → Implementer → Tester → Reviewer → Documenter
   g. KHÔNG spawn Implementer trước khi Architect approve
   h. Update TASK_BOARD.md sau mỗi subtask
   i. Khi tất cả subtasks done → tạo Final Report → gửi qua Telegram channel

2. XỬ LÝ INTERRUPT TỪ USER:
   - User hỏi tiến độ → trả lời ngắn + % từ TASK_BOARD, tiếp tục làm
   - User hỏi vấn đề khác → spawn subagent riêng, main session tiếp tục
   - User muốn dừng → hỏi lại: "Bạn có chắc muốn tạm dừng task [TÊN] không?
     Gõ 'xác nhận tạm dừng' để dừng."
     Chỉ dừng khi nhận được đúng cụm từ đó.

3. RETRY LOGIC:
   - Reviewer REJECT → Implementer retry (tối đa 3 lần)
   - Sau 3 lần fail → thử approach khác
   - Sau 2 approach fail → đánh dấu BLOCKED, báo user

4. CHỈ trả về end_turn khi:
   - Task hoàn thành VÀ Final Report đã gửi
   - Hoặc user xác nhận tạm dừng
   - Hoặc BLOCKED và đã báo user

KHÔNG hỏi lại user trong lúc làm trừ khi thực sự bị blocked.
""",
    )


def build_architect() -> Agent:
    return Agent(
        name="Architect",
        role="Senior Software Architect",
        llm="anthropic/claude-sonnet-4-6",
        tools=[filesystem_mcp],
        memory=True,
        instructions="""
Bạn là Architect Agent của TeamNoT.

TRƯỚC KHI LÀM:
1. Đọc AGENT_MEMORY.md để biết conventions và patterns
2. Đọc PROJECT_CONTEXT.md để hiểu constraints

VAI TRÒ:
- Thiết kế solution cho task được giao
- Tạo ADR (Architecture Decision Record) theo format trong PROJECT_CONTEXT.md
- Approve technical approach trước khi Implementer bắt đầu

OUTPUT BẮT BUỘC:
- ADR document đầy đủ
- File/folder structure cụ thể cần tạo
- List dependencies cần install
- API contracts (nếu có)
- Notes cho Implementer về gotchas cần chú ý

GIỚI HẠN:
- KHÔNG viết implementation code
- KHÔNG deploy bất cứ thứ gì
""",
    )


def build_implementer() -> Agent:
    return Agent(
        name="Implementer",
        role="Senior Software Developer",
        llm="openai/qwen-coder-plus",  # hoặc minimax/MiniMax-M2.7-highspeed
        tools=[filesystem_mcp],
        memory=True,
        instructions="""
Bạn là Implementer Agent của TeamNoT.

TRƯỚC KHI CODE:
1. Đọc AGENT_MEMORY.md — áp dụng TẤT CẢ conventions
2. Đọc ADR từ Architect — follow design CHÍNH XÁC
3. Kiểm tra file/folder structure đã được approve

QUY TRÌNH:
1. Tạo git branch: feature/TASK-XXX-description
2. Implement từng file theo thứ tự trong ADR
3. Sau mỗi file: tự kiểm tra logic
4. Sau khi xong tất cả: chạy linter (ruff / eslint)
5. Report kết quả cho Orchestrator

OUTPUT BẮT BUỘC:
- Danh sách file đã tạo/sửa với path đầy đủ
- Lệnh để chạy code (ví dụ: `uvicorn app.main:app --reload`)
- Kết quả linter (pass/fail)
- Điểm cần Reviewer chú ý

GIỚI HẠN:
- KHÔNG commit vào main
- KHÔNG deploy
- KHÔNG skip linter
""",
    )


def build_reviewer() -> Agent:
    return Agent(
        name="Reviewer",
        role="Tech Lead / Code Reviewer",
        llm="anthropic/claude-sonnet-4-6",
        tools=[filesystem_mcp],
        memory=True,
        instructions="""
Bạn là Reviewer Agent của TeamNoT.

NHIỆM VỤ:
Review code của Implementer và đưa ra verdict: APPROVE hoặc REJECT.

CHECKLIST BẮT BUỘC (từ PROJECT_CONTEXT.md):
Security:
- [ ] Không có secrets hardcoded
- [ ] Input validation đầy đủ
- [ ] SQL injection / path traversal / XSS
- [ ] Auth/authz đúng chỗ

Code quality:
- [ ] Logic rõ ràng
- [ ] Không code duplication
- [ ] Error handling đầy đủ
- [ ] Docstring/comment đủ

Conventions:
- [ ] Đúng pattern trong AGENT_MEMORY.md
- [ ] Naming convention nhất quán

Edge cases:
- [ ] Null/empty input
- [ ] Network timeout
- [ ] Concurrent access

OUTPUT FORMAT:
Dùng đúng Code Review Checklist format trong PROJECT_CONTEXT.md.
Nếu REJECT: mô tả issue cụ thể + file:dòng + hướng fix.
Nếu APPROVE: ghi brief summary những điểm tốt.

GIỚI HẠN:
- KHÔNG sửa code trực tiếp
- KHÔNG approve nếu có security issue nghiêm trọng
""",
    )


def build_tester() -> Agent:
    return Agent(
        name="Tester",
        role="QA Engineer",
        llm="openai/qwen-coder-plus",
        tools=[filesystem_mcp],
        memory=True,
        instructions="""
Bạn là Tester Agent của TeamNoT.

NHIỆM VỤ:
1. Viết unit tests cho code của Implementer
2. Viết integration tests (nếu có API/DB)
3. Chạy test suite
4. Report kết quả

QUYYT TẮC:
- pytest cho Python, Vitest cho TypeScript
- Target coverage: 60% minimum
- Test file: `tests/test_[module_name].py`
- Mỗi function quan trọng phải có ít nhất 1 happy path + 1 edge case test

OUTPUT:
- Số test passed/failed
- Coverage %
- Danh sách test chưa pass với mô tả lỗi
""",
    )


# ── Build team ─────────────────────────────────────────────────────
def build_team() -> Agents:
    return Agents(agents=[
        build_orchestrator(),
        build_architect(),
        build_implementer(),
        build_reviewer(),
        build_tester(),
    ])


# ── Entry point ────────────────────────────────────────────────────
def run_task(task_description: str) -> str:
    """
    Chạy một task hoàn chỉnh từ đầu đến cuối.
    Trả về final report string.
    """
    logger.info(f"Starting task: {task_description[:80]}...")
    team = build_team()
    orchestrator = build_orchestrator()

    done_criteria = """
    TASK_BOARD.md không còn task nào ở trạng thái 'In Progress' hoặc 'Todo'
    VÀ Final Report đã được tạo trong thư mục REPORTS/
    """

    result = orchestrator.run_until(
        prompt=f"""
Task mới từ user qua Telegram:
{task_description}

Hãy bắt đầu pipeline: Architect → Implementer → Tester → Reviewer → Documenter.
Cập nhật TASK_BOARD.md ở mỗi bước.
Khi xong: tạo Final Report theo format trong PROJECT_CONTEXT.md.
""",
        criteria=done_criteria,
        threshold=8.5,
        max_iterations=30,
    )

    logger.info("Task completed.")
    return str(result)


if __name__ == "__main__":
    # Test run
    import sys
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        task = "Tạo FastAPI CRUD API đơn giản cho model User (id, name, email, created_at)"
    
    report = run_task(task)
    print("\n=== FINAL REPORT ===")
    print(report)
```

---

## Bước 4 — Kết nối Telegram qua OpenClaw

OpenClaw đã kết nối Telegram rồi — thêm config để route message vào TeamNoT:

Sửa `%USERPROFILE%\.openclaw\openclaw.json`, thêm vào agents:

```json
{
  "agents": {
    "list": [
      {
        "name": "teamnot",
        "description": "TeamNoT — Autonomous software development AI team",
        "model": {
          "primary": "minimax/MiniMax-M2.7"
        },
        "tools": {
          "exec": {
            "enabled": true,
            "shell": "powershell",
            "confirmation": {
              "required": ["file_delete"]
            }
          },
          "read":  { "enabled": true },
          "write": { "enabled": true },
          "edit":  { "enabled": true },
          "web_search": { "enabled": true },
          "memory": { "enabled": true }
        },
        "skills": ["teamnot-orchestrator"],
        "channels": {
          "telegram": {
            "enabled": true
          }
        }
      }
    ]
  }
}
```

---

## Bước 5 — Tạo TeamNoT skill cho OpenClaw

Tạo file `%USERPROFILE%\.openclaw\workspace\skills\teamnot-orchestrator\SOUL.md`:

```markdown
---
name: teamnot-orchestrator
description: TeamNoT autonomous dev team — nhận task phần mềm, tự phát triển, báo cáo kết quả
tags: [coding, development, autonomous, team, crm, erp, ai, web, mobile]
---

# TeamNoT Orchestrator Skill

Khi user gửi yêu cầu phát triển phần mềm:

## Kích hoạt khi
- User mô tả một tính năng / module / project cần build
- User yêu cầu sửa bug hoặc refactor code
- User yêu cầu thêm tính năng AI (RAG, CV, LLM)
- User hỏi về tiến độ của task đang chạy

## Quy trình
1. Đọc PROJECT_CONTEXT.md để load team context
2. Chạy `python C:/Projects/TeamNoT/teamnot.py "[task description]"`
3. TeamNoT tự xử lý hoàn toàn
4. Gửi final report khi done

## Phản hồi ngay khi nhận task
"TeamNoT đã nhận task: [tên task]
Bắt đầu xử lý...
Pipeline: Architect → Implementer → Reviewer → Tester
Tôi sẽ báo cáo khi hoàn thành. Trong lúc chờ, bạn có thể hỏi tiến độ bất lúc nào."

## Xử lý interrupt
- Hỏi tiến độ → đọc TASK_BOARD.md và trả lời %
- Yêu cầu dừng → hỏi xác nhận theo quy tắc trong PROJECT_CONTEXT.md
- Câu hỏi khác → tạo subagent trả lời độc lập

## KHÔNG làm
- KHÔNG hỏi lại nhiều câu trước khi bắt đầu
- KHÔNG đợi approval từ user trừ khi BLOCKED
- KHÔNG dừng giữa chừng mà không có xác nhận
```

---

## Bước 6 — Tạo thư mục LOGS và REPORTS

```powershell
cd C:\Projects\TeamNoT
mkdir LOGS
mkdir REPORTS
mkdir ADRs

# Tạo .gitignore
@"
.env
.venv/
__pycache__/
*.pyc
LOGS/
.openclaw/
"@ | Out-File -FilePath .gitignore -Encoding utf8

# Init git
git init
git add PROJECT_CONTEXT.md TASK_BOARD.md AGENT_MEMORY.md IMPLEMENTATION_GUIDE.md .gitignore
git commit -m "chore: init TeamNoT knowledge base"
```

---

## Bước 7 — Test end-to-end

```powershell
cd C:\Projects\TeamNoT
.venv\Scripts\Activate.ps1

# Test trực tiếp
python teamnot.py "Tạo FastAPI endpoint GET /health trả về status và timestamp"
```

Kết quả mong đợi:
1. Architect tạo ADR trong `ADRs/ADR-001-health-endpoint.md`
2. Implementer tạo `app/routers/health.py`
3. Tester tạo `tests/test_health.py`, chạy pytest
4. Reviewer APPROVE
5. Documenter cập nhật README
6. Orchestrator tạo `REPORTS/TASK-001.md`
7. Report gửi qua Telegram

---

## Bước 8 — Restart OpenClaw gateway

```powershell
openclaw gateway restart
openclaw doctor --fix
```

Sau bước này, nhắn tin vào Telegram bot:
> "TeamNoT: tạo FastAPI CRUD API cho module Product (name, price, stock)"

Bot sẽ phản hồi xác nhận và bắt đầu chạy autonomously.

---

## Phase 2 checklist (sau khi Phase 1 ổn định)

- [ ] Thêm Researcher Agent
- [ ] Thêm Documenter Agent
- [ ] Context pruning cho long-running tasks
- [ ] Cost tracking (đọc MiniMax coding plan usage API)
- [ ] Multi-task queue (xử lý song song)
- [ ] GitHub integration (auto create PR)

## Phase 3 checklist — CrewAI parallel

- [ ] `pip install crewai`
- [ ] Port agent definitions sang CrewAI format
- [ ] A/B test: cùng task → PraisonAI vs CrewAI
- [ ] So sánh: thời gian, cost, code quality, số lần retry
- [ ] Document kết quả vào `AGENT_MEMORY.md`
- [ ] Quyết định framework chính hoặc hybrid

---

## Troubleshooting

| Vấn đề | Nguyên nhân | Fix |
|---|---|---|
| `ModuleNotFoundError: praisonaiagents` | Chưa activate venv | `.venv\Scripts\Activate.ps1` |
| Telegram bot không nhận message | OpenClaw gateway chưa restart | `openclaw gateway restart` |
| MCP filesystem không mount | npx chưa cài | `npm install -g npx` |
| Agent loop không dừng | criteria quá chặt | Giảm threshold từ 9.0 xuống 8.0 |
| Cost vượt limit | Task quá lớn | Chia nhỏ task, đặt subagents model rẻ hơn |
| Reviewer REJECT liên tục | Implementer không đọc AGENT_MEMORY | Kiểm tra memory scope = project |
