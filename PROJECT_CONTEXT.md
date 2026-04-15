# PROJECT_CONTEXT.md — TeamNoT
> Đây là tài liệu tri thức cốt lõi. MỌI agent phải đọc file này trước khi bắt đầu bất kỳ task nào.
> Last updated: 2026-04-14

---

## 1. Tầm nhìn & Mục tiêu

**TeamNoT** (Team No Time) là hệ thống AI multi-agent tự động phát triển phần mềm, vận hành 24/7 trên MiniPC Windows cục bộ. Người dùng giao task qua Telegram, hệ thống tự phân tích, lên kế hoạch, phân công, thực thi và báo cáo kết quả mà không cần giám sát thủ công.

**Mission:** Từ một yêu cầu phần mềm bằng ngôn ngữ tự nhiên → ra sản phẩm chạy được, có test, có tài liệu.

**Domain chuyên biệt:**
- Web / App / Mobile / Desktop applications
- CRM, ERP systems
- AI-powered features: Computer Vision (CV), LLM integration, RAG pipelines
- Vietnamese-market products (SME, administrative offices)

---

## 2. Stack kỹ thuật hệ thống

### 2.1 Runtime layer
| Component | Tool | Vai trò |
|---|---|---|
| Gateway & channel | OpenClaw | Nhận/gửi Telegram, quản lý session |
| Orchestrator model | MiniMax M2.7 | Orchestrator X (rẻ, nhanh, 24/7 loop) |
| Architect / Reviewer | Claude Sonnet 4.6 | Thiết kế, review chất lượng cao |
| Implementer / Tester | Qwen Code CLI | Code nhanh, rẻ, tool-use tốt |
| Agent framework v1 | PraisonAI | MVP — deploy nhanh, Telegram native |
| Agent framework v2 | CrewAI | So sánh hiệu quả sau khi v1 ổn định |
| Shared knowledge | Filesystem MCP | Đọc/ghi file cục bộ trên MiniPC |
| Loop engine | `agent.run_until()` | Tự iterate đến khi done |

### 2.2 MiniPC environment
- OS: Windows 11
- Python: 3.11+
- Node.js: 18+ (cho MCP filesystem server)
- Working directory: `C:/Projects/TeamNoT/`
- Telegram Bot Token: lưu trong `.env` (không commit)
- API Keys: lưu trong `.env` (không commit)

### 2.3 Stack phát triển sản phẩm (cho các project con)
**Backend:** Python (FastAPI, asyncio), Node.js (Express/Fastify)
**Frontend:** React/Next.js, Vue 3, hoặc mobile Flutter
**Database:** PostgreSQL (primary), Redis (cache), SQLite (light projects)
**AI layer:** Dify (self-hosted), n8n (automation), LangChain/LlamaIndex (RAG)
**Vision:** YOLOv8, DeepStream (Jetson), FastAPI + WebSocket
**Deploy:** Docker Compose, Nginx, GitHub Actions CI/CD
**Version control:** Git — mỗi agent làm việc trong branch riêng

---

## 3. Kiến trúc hệ thống TeamNoT

```
User (Telegram)
      │
      ▼
OpenClaw Gateway (MiniMax M2.7 — Orchestrator X)
      │
      ├── Đọc PROJECT_CONTEXT.md + TASK_BOARD.md
      │
      ├── Spawn agents theo vai trò:
      │     ├── Architect Agent     (Claude Sonnet)
      │     ├── Implementer Agent   (Qwen Code CLI)
      │     ├── Reviewer Agent      (Claude Sonnet)
      │     ├── Tester Agent        (Qwen Code CLI)
      │     ├── Researcher Agent    (MiniMax highspeed)
      │     └── Documenter Agent    (MiniMax highspeed)
      │
      ├── Shared Knowledge Layer (Filesystem MCP):
      │     ├── PROJECT_CONTEXT.md    ← file này
      │     ├── TASK_BOARD.md         ← trạng thái task
      │     ├── AGENT_MEMORY.md       ← patterns, decisions
      │     └── REPORTS/              ← báo cáo cuối mỗi task
      │
      └── end_turn → Báo cáo → Telegram
```

---

## 4. Quy tắc vận hành — BẮT BUỘC tuân thủ

### 4.1 Nguyên tắc hoạt động cốt lõi

1. **Làm đến khi xong.** Khi bắt đầu một task, Orchestrator X không kết thúc session (không trả `end_turn`) cho đến khi task hoàn thành hoặc gặp BLOCKER không thể tự giải quyết.

2. **Không hỏi lại user trong lúc làm.** Nếu cần thêm thông tin, tự research (web search, đọc docs) hoặc đưa ra assumption hợp lý nhất rồi ghi vào `AGENT_MEMORY.md`. Chỉ hỏi user nếu assumption sai sẽ dẫn đến sản phẩm hoàn toàn không đúng hướng.

3. **Báo cáo khi xong.** Sau khi hoàn thành, gửi báo cáo đầy đủ (xem format ở mục 7) qua Telegram.

4. **Xử lý interrupt từ user:**

   ```
   Nếu user nhắn trong lúc đang làm:
     → Nếu hỏi về tiến độ: trả lời ngắn gọn + % hoàn thành từ TASK_BOARD, rồi tiếp tục làm
     → Nếu hỏi task mới/vấn đề khác: spawn subagent độc lập xử lý, main session tiếp tục
     → Nếu yêu cầu tạm dừng:
         Hỏi lại: "Bạn có chắc muốn tạm dừng task [TÊN_TASK] không?
                   Task đang ở bước [BƯỚC_HIỆN_TẠI].
                   Trả lời 'xác nhận tạm dừng' để dừng."
         → Chỉ dừng khi user gõ đúng "xác nhận tạm dừng"
         → Khi dừng: lưu state vào TASK_BOARD.md (status: PAUSED)
   ```

5. **Retry logic:** Nếu Reviewer REJECT → Implementer retry với feedback cụ thể. Tối đa 3 lần retry. Nếu vẫn fail → escalate lên Orchestrator → Orchestrator thử approach khác. Nếu vẫn fail sau 2 approach → đánh dấu BLOCKED, báo user.

6. **Cost control:** Mỗi task không vượt $5 USD. Nếu ước tính vượt → xin phép user trước.

### 4.2 Thứ tự làm việc (pipeline)

```
Researcher (nếu cần)
    ↓
Architect → design + ADR
    ↓
Orchestrator approve design
    ↓
Implementer → code (trong git branch riêng)
    ↓
Tester → viết test + chạy test
    ↓
Reviewer → review code + test results
    ↓  (nếu REJECT → loop lại Implementer)
Documenter → cập nhật README, CHANGELOG
    ↓
Orchestrator → merge + final report → Telegram
```

### 4.3 Quy tắc code

- Mỗi agent làm việc trong git branch riêng: `feature/[task-id]-[description]`
- Không commit trực tiếp lên `main`
- Tất cả secrets phải trong `.env`, không hardcode
- Mỗi function/module phải có docstring tiếng Anh
- Test coverage tối thiểu: 60% cho MVP, 80% cho production
- Chạy linter (ruff cho Python, eslint cho JS) trước khi report done
- File lớn hơn 300 dòng → phải refactor thành module nhỏ hơn

### 4.4 Quy tắc memory

- Sau mỗi task: Orchestrator extract patterns → ghi vào `AGENT_MEMORY.md`
- Trước mỗi task: tất cả agents đọc `AGENT_MEMORY.md` để biết conventions
- `AGENT_MEMORY.md` là nguồn sự thật duy nhất về "cách chúng ta làm việc"

---

## 5. Agent roster — vai trò và giới hạn

### Orchestrator X
- **Model:** MiniMax M2.7
- **Vai trò:** Nhận task, decompose, assign, track, coordinate, report
- **Quyền:** Đọc/ghi tất cả shared files, spawn bất kỳ agent nào
- **Giới hạn:** KHÔNG tự implement code, KHÔNG deploy lên production mà không có human approval
- **Loop:** Chạy liên tục, chỉ kết thúc khi done hoặc user xác nhận tạm dừng

### Architect Agent
- **Model:** Claude Sonnet 4.6
- **Vai trò:** Thiết kế solution, tạo ADR (Architecture Decision Record), approve technical approach
- **Tools:** read, write, web_search (cho docs và best practices)
- **Output format:** Architecture Decision Record (xem mục 6)
- **Giới hạn:** KHÔNG viết implementation code

### Implementer Agent
- **Model:** Qwen Code CLI (hoặc MiniMax M2.7-highspeed nếu Qwen unavailable)
- **Vai trò:** Viết code theo design đã được Architect approve
- **Tools:** read, write, edit, exec (PowerShell), apply_patch
- **Quy tắc:** Đọc `AGENT_MEMORY.md` trước khi bắt đầu. Tự chạy linter sau khi xong.
- **Giới hạn:** KHÔNG deploy, KHÔNG merge vào main

### Reviewer Agent
- **Model:** Claude Sonnet 4.6
- **Vai trò:** Review code, security, edge cases, performance, conventions
- **Output:** APPROVE hoặc REJECT với checklist chi tiết (xem mục 6)
- **Giới hạn:** Không sửa code trực tiếp, chỉ feedback

### Tester Agent
- **Model:** Qwen Code CLI
- **Vai trò:** Viết unit test, integration test, chạy test suite
- **Tools:** read, write, exec
- **Output:** Test report với pass/fail + coverage %

### Researcher Agent
- **Model:** MiniMax M2.7-highspeed
- **Vai trò:** Tìm tài liệu, so sánh thư viện, tìm ví dụ code
- **Tools:** web_search, web_fetch
- **Output:** Research summary với recommendation

### Documenter Agent
- **Model:** MiniMax M2.7-highspeed
- **Vai trò:** Cập nhật README, API docs, CHANGELOG
- **Tools:** read, write
- **Output:** Updated docs files

---

## 6. Output format chuẩn

### 6.1 Architecture Decision Record (ADR)
```markdown
# ADR-[số] — [Tên quyết định]
Date: YYYY-MM-DD
Status: Proposed | Accepted | Deprecated

## Context
[Vấn đề cần giải quyết]

## Decision
[Quyết định đã chọn]

## Alternatives considered
- Option A: [mô tả] — lý do bỏ qua
- Option B: [mô tả] — lý do bỏ qua

## Consequences
- Positive: ...
- Negative/Tradeoffs: ...

## Implementation notes
[Hướng dẫn implement cụ thể]
```

### 6.2 Code Review Checklist
```markdown
## Review: [Task ID] — [tên task]
Reviewer: [agent name]
Date: YYYY-MM-DD HH:MM

### Verdict: APPROVE / REJECT

### Security
- [ ] Không có secrets hardcoded
- [ ] Input validation đầy đủ
- [ ] SQL injection / path traversal / XSS được xử lý
- [ ] Auth/authz đúng chỗ

### Code quality
- [ ] Logic rõ ràng, dễ đọc
- [ ] Không có code duplication đáng kể
- [ ] Error handling đầy đủ
- [ ] Docstring/comment đủ

### Conventions
- [ ] Đúng pattern trong AGENT_MEMORY.md
- [ ] Naming convention nhất quán
- [ ] File structure đúng chuẩn project

### Edge cases
- [ ] Null/empty input xử lý
- [ ] Network timeout xử lý
- [ ] Concurrent access xử lý (nếu cần)

### Issues (nếu REJECT)
1. [Mô tả issue cụ thể + vị trí file:dòng]
2. ...
```

### 6.3 Final Report (gửi Telegram)
```markdown
## TeamNoT — Báo cáo hoàn thành

**Task:** [Tên task]
**Thời gian:** [start] → [end] ([duration])
**Status:** DONE / DONE_WITH_WARNINGS / BLOCKED

### Đã làm
- [x] [Mô tả việc đã làm 1]
- [x] [Mô tả việc đã làm 2]

### Kết quả
- Files created/modified: [danh sách]
- Tests: [X passed / Y failed] — coverage [Z]%
- Branch: `feature/[task-id]-[description]` (sẵn sàng merge)

### Warnings (nếu có)
- [Mô tả warning]

### Blockers (nếu BLOCKED)
- [Mô tả blocker cụ thể + cần user làm gì]

### Assumptions đã dùng
- [Assumption 1]
- [Assumption 2]

### Bước tiếp theo gợi ý
- [ ] [Việc user cần làm: review, merge, deploy, ...]
- [ ] [Task tiếp theo nên làm]
```

---

## 7. Shared files — schema và conventions

### TASK_BOARD.md
```markdown
# TASK_BOARD — TeamNoT
Updated: YYYY-MM-DD HH:MM

## Active session
Task: [tên]
Started: HH:MM
Progress: [X]%
Current step: [bước đang làm]

## Todo
- [ ] [TASK-001] [Tên task] — Priority: HIGH/MED/LOW

## In Progress
- [~] [TASK-002] [Tên task] — Assigned: [agent] — Started: HH:MM — Step: [bước]

## Blocked
- [!] [TASK-003] [Tên task] — Blocker: [mô tả] — Waiting: user/external

## Done
- [x] [TASK-004] [Tên task] — Done: HH:MM — Report: REPORTS/TASK-004.md

## Paused
- [||] [TASK-005] [Tên task] — Paused at: [bước] — Reason: user request
```

### AGENT_MEMORY.md
```markdown
# AGENT_MEMORY — TeamNoT
> Patterns và decisions tích lũy qua các task. Đọc TRƯỚC khi bắt đầu task mới.

## Code conventions
[Được populate qua thời gian]

## Known gotchas
[Lỗi hay gặp và cách fix]

## Library decisions
[Thư viện nào dùng cho use case nào]

## Architecture patterns
[Pattern nào đã được approve cho loại task nào]
```

---

## 8. Phased rollout plan

### Phase 1 — PraisonAI MVP (Tuần 1–2)
**Mục tiêu:** Hệ thống chạy được, nhận task qua Telegram, báo cáo kết quả

**Deliverables:**
- [ ] PraisonAI cài đặt và kết nối OpenClaw
- [ ] 4 agents cơ bản: Orchestrator, Architect, Implementer, Reviewer
- [ ] Filesystem MCP cho shared knowledge
- [ ] TASK_BOARD + AGENT_MEMORY hoạt động
- [ ] `run_until` loop với retry logic
- [ ] Telegram interrupt handling (pause/confirm logic)
- [ ] Final report format chuẩn
- [ ] Test với task thực: "Tạo FastAPI CRUD API cho module User"

**Success criteria:** Bot nhận task qua Telegram, tự làm trong 15–30 phút, gửi báo cáo, code chạy được.

### Phase 2 — Stable + Full team (Tuần 3–4)
**Mục tiêu:** Thêm Tester, Researcher, Documenter; polish loop

**Deliverables:**
- [ ] Tester Agent hoạt động (pytest / jest)
- [ ] Researcher Agent với web search
- [ ] Documenter Agent
- [ ] Context pruning để tránh overflow
- [ ] Cost tracking per task
- [ ] Multi-task queue (xử lý nhiều task song song)
- [ ] Subagent isolation cho interrupt handling

### Phase 3 — CrewAI parallel (Tuần 5–6)
**Mục tiêu:** Triển khai CrewAI song song để so sánh

**Deliverables:**
- [ ] CrewAI setup với cùng agent roles
- [ ] A/B test cùng task trên PraisonAI vs CrewAI
- [ ] Benchmark: thời gian, cost, output quality
- [ ] Chọn framework tốt hơn hoặc hybrid

### Phase 4 — Optimize (Tháng 2+)
- Model routing tự động theo task type
- RAG cho codebase knowledge
- GitHub integration (auto PR)
- Dashboard monitoring (Telegram inline keyboard)

---

## 9. Security & Safety rules

1. **Không deploy lên production** mà không có lệnh tường minh từ user
2. **Không xóa file/database** mà không có xác nhận
3. **Không commit lên `main`** — chỉ làm việc trong feature branch
4. **Không lưu secrets** vào bất kỳ file nào ngoài `.env`
5. **Confirmation gate** cho mọi action không thể undo: xóa, deploy, publish
6. **Log mọi action** vào `LOGS/YYYY-MM-DD.log`
7. **Cost limit:** Báo user nếu ước tính task > $5 USD trước khi bắt đầu

---

## 10. Glossary

| Term | Nghĩa |
|---|---|
| TeamNoT | Tên hệ thống — "Team No Time" |
| Orchestrator X | Agent điều phối chính, chạy 24/7 |
| end_turn | Signal kết thúc agent loop (chỉ dùng khi thực sự xong) |
| ADR | Architecture Decision Record |
| BLOCKER | Vấn đề không thể tự giải quyết, cần user can thiệp |
| PAUSED | Task tạm dừng theo yêu cầu user (phải xác nhận) |
| run_until | Cơ chế loop tự iterate đến khi đạt criteria |
| Feature branch | Git branch riêng cho mỗi task |
