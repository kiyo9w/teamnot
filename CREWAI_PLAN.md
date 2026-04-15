# CREWAI_PLAN.md — TeamNoT Phase 3
> Kế hoạch triển khai CrewAI song song để so sánh với PraisonAI.
> Bắt đầu sau khi Phase 1+2 (PraisonAI) ổn định ít nhất 2 tuần.

---

## Lý do thử CrewAI

- 44,300+ GitHub stars, 5.2M monthly downloads — cộng đồng lớn nhất trong Python agent frameworks
- Role-playing architecture phù hợp với TeamNoT concept
- Streaming tool calls (thêm Jan 2026) — cải thiện real-time feedback
- YAML config rõ ràng, dễ define agent roles

## Điểm khác biệt cần test

| Dimension | PraisonAI | CrewAI |
|---|---|---|
| Setup time | 2 giờ | ~1 ngày |
| Telegram native | Có (praisonai[claw]) | Cần build thêm |
| run_until loop | Native | Phải implement bằng while loop + evaluator |
| Agent communication | Sequential + memory | Hierarchical process hoặc sequential |
| Cost control | Built-in | Manual implement |
| Debug visibility | Medium | Good (verbose mode) |

## Implementation plan

### Cài đặt
```bash
pip install crewai crewai-tools
```

### Agent definitions (CrewAI format)
```python
from crewai import Agent, Task, Crew, Process
from crewai_tools import FileReadTool, FileWriterTool

architect = Agent(
    role="Senior Software Architect",
    goal="Design robust, scalable software solutions",
    backstory="""
    Bạn là Architect chuyên về Python/FastAPI, React, PostgreSQL.
    Luôn đọc AGENT_MEMORY.md trước khi thiết kế.
    Output: ADR document đầy đủ.
    """,
    llm="claude-sonnet-4-6",
    tools=[FileReadTool(), FileWriterTool()],
    verbose=True,
    memory=True,
)

implementer = Agent(
    role="Senior Software Developer",
    goal="Implement clean, tested, production-ready code",
    backstory="""
    Bạn là Implementer. Chỉ code sau khi Architect approve.
    Luôn theo conventions trong AGENT_MEMORY.md.
    Chạy linter trước khi report done.
    """,
    llm="qwen-coder-plus",
    tools=[FileReadTool(), FileWriterTool()],
    verbose=True,
    memory=True,
)

reviewer = Agent(
    role="Tech Lead Code Reviewer",
    goal="Ensure code quality, security, and convention compliance",
    backstory="""
    Bạn là Reviewer. Review theo checklist trong PROJECT_CONTEXT.md.
    Output: APPROVE hoặc REJECT với chi tiết cụ thể.
    """,
    llm="claude-sonnet-4-6",
    tools=[FileReadTool()],
    verbose=True,
)
```

### Evaluator loop (thay thế run_until)
```python
def run_until_done(task_description: str, max_retries: int = 3) -> str:
    for attempt in range(max_retries):
        crew = Crew(
            agents=[architect, implementer, reviewer],
            tasks=build_tasks(task_description),
            process=Process.sequential,
            verbose=True,
        )
        result = crew.kickoff()
        
        # Evaluate nếu done
        evaluator = Agent(role="QA Evaluator", ...)
        score = evaluator.evaluate(result)
        if score >= 8.5:
            return result
        # Else retry với feedback
    return result  # fallback sau max_retries
```

## Benchmark protocol

Sau khi cả 2 framework chạy được, test với cùng 5 task chuẩn:

1. **Task A (Simple):** Tạo FastAPI CRUD cho 1 model
2. **Task B (Medium):** Thêm JWT auth vào project có sẵn
3. **Task C (Complex):** RAG pipeline với FastAPI + PostgreSQL + LlamaIndex
4. **Task D (AI feature):** YOLOv8 object detection endpoint
5. **Task E (Full app):** CRM module: Contacts + Deals + Activities

### Metrics đo
- Thời gian hoàn thành (wall clock)
- API cost (USD)
- Code quality score (Reviewer)
- Số lần retry
- Test pass rate
- Human review rating (1–10)

### Quyết định sau benchmark
- Nếu PraisonAI tốt hơn toàn diện → giữ PraisonAI, bỏ CrewAI
- Nếu CrewAI tốt hơn → migrate toàn bộ sang CrewAI
- Nếu mỗi framework tốt ở loại task khác nhau → hybrid routing
  (ví dụ: simple task → PraisonAI, complex task → CrewAI)

Ghi kết quả vào `AGENT_MEMORY.md` section "Framework benchmark".
