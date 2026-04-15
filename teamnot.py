"""
TeamNoT — Autonomous Software Development AI Team
Chạy: python teamnot.py
Hoặc: python teamnot.py "Tạo module CRM Contacts với CRUD, search, và export CSV"
"""
import os
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from praisonaiagents import Agent, Agents, MCP

load_dotenv()

ROOT = Path(os.getenv("TEAMNOT_ROOT", "C:/Users/Jenky - MiniPC/Desktop/Project/TeamNoT"))
PROJECTS_ROOT = Path(os.getenv("TEAMNOT_PROJECTS_ROOT", "C:/Users/Jenky - MiniPC/Desktop/Project"))

# Tạo thư mục LOGS nếu chưa có
(ROOT / "LOGS").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(ROOT / "LOGS" / f"{datetime.now().strftime('%Y-%m-%d')}.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("TeamNoT")

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
    """Load nội dung các file shared context để inject vào agent instructions."""
    files = ["PROJECT_CONTEXT.md", "TASK_BOARD.md", "AGENT_MEMORY.md"]
    content = []
    for f in files:
        path = ROOT / f
        if path.exists():
            content.append(f"=== {f} ===\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(content)

# ── Agent definitions ──────────────────────────────────────────────
def build_orchestrator() -> Agent:
    """Tạo Orchestrator X agent — điều phối toàn bộ pipeline."""
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
    """Tạo Architect Agent — thiết kế solution và tạo ADR."""
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
- ADR document đầy đủ lưu vào ADRs/ADR-XXX-tên.md
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
    """Tạo Implementer Agent — viết code theo thiết kế đã approve."""
    return Agent(
        name="Implementer",
        role="Senior Software Developer",
        llm="openai/qwen-coder-plus",
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
    """Tạo Reviewer Agent — review code và đưa verdict APPROVE/REJECT."""
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
    """Tạo Tester Agent — viết và chạy tests."""
    return Agent(
        name="Tester",
        role="QA Engineer",
        llm=os.getenv("QWEN_MODEL", "openai/qwen-coder-plus"),
        tools=[filesystem_mcp],
        memory=True,
        instructions="""
Bạn là Tester Agent của TeamNoT.

TRƯỚC KHI LÀM: Đọc AGENT_MEMORY.md để biết testing conventions.

NHIỆM VỤ:
1. Đọc code của Implementer
2. Viết unit tests: tests/test_[module].py
3. Mỗi function quan trọng: 1 happy path + 1 edge case tối thiểu
4. Chạy: pytest --cov=app --cov-report=term-missing -v
5. Report kết quả

TARGET: 60% coverage minimum.

OUTPUT BẮT BUỘC:
- Số test passed / failed
- Coverage % từng module
- Danh sách test fail với traceback ngắn gọn

GIỚI HẠN: KHÔNG sửa production code. Chỉ viết test code.
""",
    )


def build_researcher() -> Agent:
    """Tạo Researcher Agent — research thư viện và best practices."""
    return Agent(
        name="Researcher",
        role="Technical Researcher",
        llm="openai/minimax/MiniMax-M2.7-highspeed",
        tools=[filesystem_mcp],
        memory=True,
        instructions="""
Bạn là Researcher Agent của TeamNoT.

ĐƯỢC GỌI KHI: Task dùng thư viện chưa có trong AGENT_MEMORY.md,
hoặc cần so sánh approach, hoặc cần best practices mới nhất.

QUY TRÌNH:
1. Web search keywords kỹ thuật liên quan
2. So sánh options theo: performance, maintenance, licensing
3. Đưa recommendation rõ ràng với lý do

OUTPUT:
- Summary findings (tối đa 400 words)
- Recommendation: "Dùng X vì Y, không dùng Z vì W"
- Code example ngắn nếu có

GIỚI HẠN: Không implement code. Chỉ research và recommend.
""",
    )


def build_documenter() -> Agent:
    """Tạo Documenter Agent — cập nhật README, CHANGELOG, AGENT_MEMORY."""
    return Agent(
        name="Documenter",
        role="Technical Writer",
        llm="openai/minimax/MiniMax-M2.7-highspeed",
        tools=[filesystem_mcp],
        memory=True,
        instructions="""
Bạn là Documenter Agent của TeamNoT.

LUÔN CẬP NHẬT SAU MỖI TASK:
1. README.md — thêm feature mới vào mục Features
2. CHANGELOG.md — thêm entry:
   ## [YYYY-MM-DD] - Task ID
   ### Added / Fixed / Changed
3. AGENT_MEMORY.md — extract lessons:
   - Convention mới → Code conventions
   - Gotcha mới → Known gotchas
   - Library mới → Library decisions

OUTPUT: Danh sách file đã cập nhật + nội dung thay đổi tóm tắt.
GIỚI HẠN: Chỉ cập nhật docs. Không chạm production code.
""",
    )


# ── Build team ─────────────────────────────────────────────────────
def build_team() -> Agents:
    """Phase 2: full 7-agent team."""
    return Agents(agents=[
        build_orchestrator(),
        build_researcher(),
        build_architect(),
        build_implementer(),
        build_tester(),
        build_reviewer(),
        build_documenter(),
    ])


# ── Entry point ────────────────────────────────────────────────────
def run_task(task, task_id: str = None) -> str:
    """
    Phase 2: nhận QueuedTask object hoặc string description.
    Pipeline: Researcher? → Architect → Implementer → Tester → Reviewer → Documenter

    Args:
        task: str description hoặc QueuedTask object
        task_id: optional override cho task ID (khi gọi bằng string)
    """
    import time
    from cost_tracker import task_total

    # Hỗ trợ cả string (gọi trực tiếp) và QueuedTask object (từ queue)
    if isinstance(task, str):
        description = task
        if not task_id:
            task_id = f"TASK-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    else:
        description = task.description
        task_id     = task.id

    logger.info(f"[{task_id}] Pipeline start: {description[:80]}")
    start_time = time.time()

    orchestrator = build_orchestrator()

    done_criteria = """
    Tất cả bước trong pipeline đều hoàn thành:
    - Architect: ADR đã tạo và approved
    - Implementer: code complete, linter passed
    - Tester: test suite đã chạy, kết quả documented
    - Reviewer: đã cho verdict APPROVE
    - Documenter: README/CHANGELOG/AGENT_MEMORY đã cập nhật
    VÀ Final Report đã tạo trong thư mục REPORTS/
    """

    result = orchestrator.run_until(
        prompt=f"""
Task ID: {task_id}
Mô tả: {description}

PIPELINE (theo thứ tự nghiêm ngặt):
1. Researcher → chỉ gọi nếu task dùng thư viện chưa có trong AGENT_MEMORY.md
2. Architect → thiết kế, tạo ADR tại ADRs/{task_id}.md
3. Implementer → code trên branch feature/{task_id}, chạy linter
4. Tester → viết pytest, chạy coverage, report kết quả
5. Reviewer → review checklist đầy đủ, APPROVE hoặc REJECT+feedback
   (Nếu REJECT: Implementer retry tối đa 3 lần trước khi escalate)
6. Documenter → cập nhật README, CHANGELOG, AGENT_MEMORY

Cập nhật TASK_BOARD.md sau mỗi bước.
Tạo Final Report tại REPORTS/{task_id}.md khi hoàn thành.
""",
        criteria=done_criteria,
        threshold=8.5,
        max_iterations=40,
    )

    elapsed = round((time.time() - start_time) / 60, 1)
    logger.info(f"[{task_id}] Done in {elapsed} min — cost: ${task_total(task_id):.3f}")
    return str(result)


def run_queue_loop():
    """Phase 2: chạy queue loop song song (blocking — dùng cho cli.py loop)."""
    from task_queue import TaskQueue
    queue = TaskQueue()
    queue.run_parallel_loop(run_task)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        task = "Tạo FastAPI CRUD API đơn giản cho model User (id, name, email, created_at)"

    report = run_task(task)
    print("\n=== FINAL REPORT ===")
    print(report)
