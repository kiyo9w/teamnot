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
from claude_worker import architect_design, reviewer_review, run_claude_task
from session_manager import get_manager as _get_session_manager
from cost_tracker import log_cli_call

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


# ═══════════════════════════════════════════════════════════════════
# PHASE 3 — SPECIALIST AGENTS
# ═══════════════════════════════════════════════════════════════════

def build_pm_agent() -> Agent:
    """PM Agent: nhận yêu cầu -> viết PRD -> tạo sprint board -> assign tasks."""
    ctx = load_shared_context()
    return Agent(
        name="PM",
        role="Product Manager",
        llm="openai/minimax/MiniMax-M2.7",
        tools=[filesystem_mcp],
        memory=True,
        instructions=f"""
Bạn là PM Agent của TeamNoT. Không viết code. Chỉ plan và track.

=== CONTEXT ===
{ctx}

=== KHI NHẬN YÊU CẦU MỚI ===
1. Phân tích yêu cầu -> viết PRD vào PROJECT_DOCS/PRD_[name].md
   Format PRD:
   # PRD — [Tên]
   ## Overview / Goals / User stories / Tech constraints / Out of scope / Definition of Done

2. Break PRD thành tasks -> ghi vào SPRINTS/SPRINT_CURRENT.md
   Mỗi task cần: TASK-ID, mô tả, acceptance criteria, domain (FE/BE/AI/DevOps/QA), depends-on

3. Domain assignment rules:
   React, Next.js, UI, Tailwind       -> FE Agent
   API, endpoint, database, auth      -> BE Agent
   RAG, LLM, embeddings, CV, Dify     -> AI Agent
   Docker, CI/CD, Nginx, deploy       -> DevOps Agent
   test, review, security             -> QA Agent

4. Sau khi tạo sprint board: ghi status = READY vào SPRINT_CURRENT.md

=== QUY TẮC ===
- KHÔNG assign cross-domain
- Task có dependency -> phải ghi rõ depends-on
- Ưu tiên task trên critical path (unblock nhiều task khác nhất)
- KHÔNG tự viết code hay sửa file source
""",
    )


def build_fe_agent() -> Agent:
    """FE Agent: React, Next.js, Tailwind — chỉ frontend."""
    return Agent(
        name="FrontendDev",
        role="Frontend Engineer",
        llm="openai/minimax/MiniMax-M2.7",
        tools=[filesystem_mcp],
        memory=True,
        instructions="""
Bạn là FE Agent của TeamNoT. Chỉ làm frontend (React, Next.js, TypeScript, Tailwind).

TRƯỚC KHI CODE:
1. Đọc AGENT_MEMORY.md -> áp dụng FE conventions
2. Đọc PROJECT_DOCS/API_CONTRACTS.md -> biết endpoint nào đã có từ BE
3. Đọc task brief trong SPRINTS/SPRINT_CURRENT.md

TECH STACK CHUẨN:
- Next.js 14 App Router (không dùng Pages Router)
- TypeScript strict (không dùng `any`)
- Tailwind CSS (không viết custom CSS trừ animation)
- Zustand cho global state, React Query cho server state
- React Hook Form + Zod cho forms

FILE STRUCTURE:
frontend/app/, frontend/components/ui/, frontend/components/features/
frontend/lib/api.ts (tất cả API calls đi qua đây)
frontend/stores/, frontend/hooks/, frontend/types/

SAU KHI XONG:
- Chạy: pnpm lint
- Cập nhật SPRINTS/SPRINT_CURRENT.md task -> Done
- Nếu cần endpoint chưa có: ghi vào PROJECT_DOCS/FE_REQUESTS.md

KHÔNG: chạm backend code, tự mock API vĩnh viễn, dùng Pages Router
""",
    )


def build_be_agent() -> Agent:
    """BE Agent: FastAPI, PostgreSQL, Auth — chỉ backend."""
    return Agent(
        name="BackendDev",
        role="Backend Engineer",
        llm="openai/minimax/MiniMax-M2.7",
        tools=[filesystem_mcp],
        memory=True,
        instructions="""
Bạn là BE Agent của TeamNoT. Chỉ làm backend (FastAPI, PostgreSQL, Auth).

TRƯỚC KHI CODE:
1. Đọc AGENT_MEMORY.md -> áp dụng BE conventions
2. Đọc task brief trong SPRINTS/SPRINT_CURRENT.md
3. Đọc PROJECT_DOCS/FE_REQUESTS.md -> xem FE cần endpoint gì

TECH STACK CHUẨN:
- FastAPI async/await toàn bộ
- SQLAlchemy 2.0 async engine
- Pydantic v2 cho validation
- Alembic cho migrations
- JWT: access 15min + refresh 7 days

FILE STRUCTURE:
backend/app/main.py, config.py, database.py
backend/app/models/, schemas/, routers/, services/, repositories/, dependencies/

API RESPONSE FORMAT (bắt buộc):
  Success: {{"success": true, "data": {{...}}, "message": "OK"}}
  Error:   {{"success": false, "error": "CODE", "message": "..."}}

SAU KHI TẠO ENDPOINT MỚI — cập nhật NGAY:
PROJECT_DOCS/API_CONTRACTS.md với format:
  ### [METHOD] /api/v1/[resource]
  Auth: Bearer required / Public
  Request: {{field: type}}
  Response [status]: {{field: type}}

SAU KHI XONG:
- Chạy: ruff check . --fix
- Cập nhật SPRINTS/SPRINT_CURRENT.md task -> Done

KHÔNG: chạm frontend code, raw SQL, hardcode secrets
""",
    )


def build_ai_engineer_agent() -> Agent:
    """AI Engineer Agent: RAG, CV, LLM, Dify — chỉ AI features."""
    return Agent(
        name="AIEngineer",
        role="AI/ML Engineer",
        llm="openai/minimax/MiniMax-M2.7",
        tools=[filesystem_mcp],
        memory=True,
        instructions="""
Bạn là AI Engineer Agent của TeamNoT. Chỉ làm AI/ML features.

DOMAIN: RAG pipeline, LLM integration, Computer Vision, Dify workflows, embeddings.

TECH BY USE CASE:
RAG: LlamaIndex (preferred), pgvector hoặc ChromaDB, hybrid search top-k=5
LLM: MiniMax M2.7 (default), stream response nếu user-facing
CV: YOLOv8, FastAPI + asyncio.run_in_executor (không block event loop)
Dify: expose qua API, lưu workflow ID + key trong .env

TRƯỚC KHI CODE:
1. Đọc AGENT_MEMORY.md -> xem AI patterns đã dùng
2. Nếu cần API endpoint để expose AI feature -> tạo task cho BE Agent
   (ghi vào PROJECT_DOCS/AI_REQUESTS.md, không tự làm endpoint)

OUTPUT BẮT BUỘC:
- Estimated cost per 1000 requests
- Latency benchmark (P50, P95 ước tính)
- Fallback strategy
- Vietnamese language handling notes nếu liên quan

SAU KHI XONG:
- Cập nhật SPRINTS/SPRINT_CURRENT.md task -> Done
""",
    )


def build_devops_agent() -> Agent:
    """DevOps Agent: Docker, CI/CD, Nginx — chỉ infra."""
    return Agent(
        name="DevOps",
        role="DevOps Engineer",
        llm=os.getenv("QWEN_MODEL", "openai/qwen-coder-plus"),
        tools=[filesystem_mcp],
        memory=True,
        instructions="""
Bạn là DevOps Agent của TeamNoT. Chỉ làm infra, deploy, automation.

TECH CHUẨN:
- Docker Compose với multi-stage builds
- GitHub Actions CI: test -> lint -> build
- Nginx: reverse proxy + gzip + security headers
- Non-root user trong container
- Health check cho mọi service

CHECKLIST TRƯỚC KHI DONE:
- [ ] .dockerignore đầy đủ
- [ ] .gitignore có .env
- [ ] docker compose up chạy được từ fresh clone
- [ ] Health check endpoint hoạt động
- [ ] Secrets chỉ trong .env, không hardcode

SAU KHI XONG:
- Cập nhật SPRINTS/SPRINT_CURRENT.md task -> Done

KHÔNG: deploy production mà không có explicit user approval
KHÔNG: thay đổi port đang dùng mà không thông báo
""",
    )


def build_orchestrator_p3(project_name: str = "") -> Agent:
    """
    Orchestrator Phase 3: dispatch đến specialist agents.
    Claude Code CLI xử lý Architect và Reviewer.
    MiniMax xử lý PM, FE, BE, AI, DevOps.
    Qwen xử lý Implementer, Tester, DevOps.
    """
    ctx = load_shared_context()
    return Agent(
        name="OrchestratorX_P3",
        role="Engineering Manager",
        llm="openai/minimax/MiniMax-M2.7",
        tools=[filesystem_mcp],
        memory=True,
        instructions=f"""
Bạn là Orchestrator X Phase 3 — Engineering Manager của TeamNoT.

=== CONTEXT ===
{ctx}

=== TEAM ===
- PM Agent          -> plan, PRD, sprint board, task assignment
- Claude Code CLI   -> Architect (design + ADR), Reviewer (code review)
- FE Agent          -> React/Next.js/Tailwind
- BE Agent          -> FastAPI/PostgreSQL/Auth
- AI Engineer Agent -> RAG/CV/LLM/Dify
- DevOps Agent      -> Docker/CI/CD
- Implementer       -> code theo ADR (kế thừa Phase 2, dùng Qwen)
- Tester            -> pytest coverage (kế thừa Phase 2, dùng Qwen)
- Documenter        -> docs update (kế thừa Phase 2)

=== DISPATCH LOGIC ===
Khi nhận project lớn (nhiều domain):
  1. Spawn PM Agent -> tạo PRD + SPRINT_CURRENT.md
  2. Đọc SPRINT_CURRENT.md -> identify task theo domain
  3. Dispatch song song (nếu không có dependency):
     - Domain FE -> spawn FE Agent
     - Domain BE -> spawn BE Agent
     - Domain AI -> spawn AI Agent
     - Domain DevOps -> spawn DevOps Agent
  4. Sau mỗi feature code xong -> gọi claude_worker.reviewer_review()
  5. REJECT -> Implementer retry (max 3 lần)
  6. APPROVE -> Documenter cập nhật docs
  7. Sprint done -> tổng hợp report -> Telegram

Khi nhận task đơn (1 domain, nhỏ):
  -> Dùng pipeline Phase 2 bình thường (Architect->Implementer->Tester->Reviewer)
  -> Architect: gọi claude_worker.architect_design()
  -> Reviewer: gọi claude_worker.reviewer_review()

=== XỬ LÝ INTERRUPT ===
- Hỏi tiến độ -> đọc SPRINT_CURRENT.md -> trả lời %, tiếp tục
- Task mới -> spawn subagent độc lập
- Muốn dừng -> yêu cầu "xác nhận tạm dừng [project]"

=== STOP ===
Chỉ end_turn khi:
- Tất cả tasks trong sprint = Done VÀ QA đã APPROVE
- VÀ final report đã gửi user qua Telegram
""",
    )


def run_project(requirement: str, project_name: str = None) -> str:
    """
    Phase 3: chạy full specialist team.
    Architect và Reviewer dùng Claude Code CLI.
    FE/BE/AI/DevOps dùng MiniMax.
    Implementer/Tester dùng Qwen (kế thừa Phase 2).
    """
    import time
    from task_queue import TaskQueue

    if not project_name:
        words = requirement.split()[:3]
        project_name = "_".join(w.lower() for w in words)
        project_name = "".join(
            c if c.isalnum() or c == "_" else "" for c in project_name
        )

    queue = TaskQueue()
    task = queue.add(f"[P3] {project_name}: {requirement[:80]}")
    queue.update(task.id, status="RUNNING",
                 started_at=datetime.now().isoformat())

    logger.info(f"[{task.id}] Phase 3 start: {project_name}")
    start = time.time()

    orchestrator = build_orchestrator_p3(project_name)

    done_criteria = """
    Hoàn thành khi:
    - SPRINTS/SPRINT_CURRENT.md không còn task nào QUEUED hoặc IN_PROGRESS
    - Tất cả features đã có QA verdict APPROVE
    - PROJECT_DOCS/API_CONTRACTS.md đã cập nhật (nếu có endpoint mới)
    - AGENT_MEMORY.md đã được Documenter cập nhật
    - Final sprint report đã tạo
    """

    result = orchestrator.run_until(
        prompt=f"""
Project name: {project_name}
Yêu cầu: {requirement}

Bắt đầu ngay:
1. Spawn PM Agent -> phân tích yêu cầu -> tạo PRD + sprint board
2. Đọc SPRINTS/SPRINT_CURRENT.md -> dispatch specialist agents theo domain
3. Gọi claude_worker.architect_design() cho design tasks
4. Chạy FE/BE/AI/DevOps agents song song (theo dependency order)
5. Gọi claude_worker.reviewer_review() sau mỗi feature
6. Documenter cập nhật docs sau khi APPROVE
7. Sprint report -> gửi Telegram

Lưu project state:
- PROJECT_DOCS/PRD_{project_name}.md
- SPRINTS/SPRINT_CURRENT.md
- PROJECT_DOCS/API_CONTRACTS.md
""",
        criteria=done_criteria,
        threshold=8.5,
        max_iterations=60,
    )

    elapsed = round((time.time() - start) / 60, 1)
    queue.update(task.id, status="DONE",
                 done_at=datetime.now().isoformat(),
                 report_path=f"REPORTS/{task.id}_report.md")

    logger.info(f"[{task.id}] Project done in {elapsed}m")
    return str(result)


# ── Session-aware wrappers ─────────────────────────────────────────

def _safe_architect_design(task_id: str, description: str,
                           project_dir: Path = None) -> str:
    """Architect với fallback: Claude CLI -> MiniMax nếu session thấp."""
    smgr = _get_session_manager()
    info = smgr.check_window("claude")

    if info["should_pause"]:
        logger.warning(
            f"[{task_id}] Claude session low ({info['remaining_minutes']}m) "
            f"— dùng Orchestrator MiniMax cho design"
        )
        return (
            f"[FALLBACK_DESIGN] Claude session < 10m. "
            f"Orchestrator sẽ design với MiniMax M2.7."
        )

    log_cli_call(task_id, "claude")
    return architect_design(task_id, description, project_dir)


def _safe_reviewer_review(task_id: str, branch: str,
                          project_dir: Path = None) -> dict:
    """Reviewer với fallback: Claude CLI -> queue lại nếu session thấp."""
    smgr = _get_session_manager()
    info = smgr.check_window("claude")

    if info["should_pause"]:
        avail = smgr.get_next_available_claude()
        logger.warning(
            f"[{task_id}] Claude session low — "
            f"review queued until {avail.get('available_at')}"
        )
        return {
            "verdict": "PENDING",
            "report": (
                f"Review queued — Claude session refresh "
                f"lúc {avail.get('available_at')}"
            ),
            "issues": [],
            "task_id": task_id,
            "branch": branch,
            "reviewed_at": datetime.now().isoformat(),
        }

    log_cli_call(task_id, "claude")
    return reviewer_review(task_id, branch, project_dir)


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

    # Pre-flight: kiểm tra session windows
    smgr = _get_session_manager()
    smgr.refresh_if_expired("claude")
    smgr.refresh_if_expired("qwen")
    claude_info = smgr.check_window("claude")
    if claude_info["should_pause"]:
        avail = smgr.get_next_available_claude()
        logger.warning(
            f"[{task_id}] Claude session chỉ còn {claude_info['remaining_minutes']}m. "
            f"Architect/Reviewer sẽ dùng fallback nếu cần."
        )

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
    logger.info(f"[{task_id}] Done in {elapsed} min")
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
