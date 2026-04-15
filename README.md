# TeamNoT — Autonomous Software Development AI Team

> "Team No Time" — Giao task, đi ngủ, thức dậy thấy code xong.

TeamNoT là hệ thống AI multi-agent tự động phát triển phần mềm, vận hành 24/7 trên MiniPC Windows cục bộ. Nhận task qua Telegram, tự phân tích, thiết kế, code, test, review và báo cáo.

---

## Tài liệu tri thức

| File | Mục đích | Ai đọc |
|---|---|---|
| `PROJECT_CONTEXT.md` | Tri thức cốt lõi, quy tắc, agent roles | TẤT CẢ agents |
| `TASK_BOARD.md` | Trạng thái task real-time | Orchestrator X |
| `AGENT_MEMORY.md` | Patterns, conventions tích lũy | TẤT CẢ agents |
| `IMPLEMENTATION_GUIDE.md` | Hướng dẫn deploy kỹ thuật | Human developer |
| `CREWAI_PLAN.md` | Kế hoạch Phase 3 (CrewAI) | Human developer |

## Cách dùng

Nhắn tin vào Telegram bot:
```
TeamNoT: tạo module CRM Contacts với CRUD, search, và export CSV
```

Bot phản hồi xác nhận → bắt đầu chạy autonomously → báo cáo khi xong.

## Stack

- **Runtime:** OpenClaw + PraisonAI (Phase 1–2), CrewAI (Phase 3 so sánh)
- **Orchestrator:** MiniMax M2.7
- **Architect / Reviewer:** Claude Sonnet 4.6
- **Implementer / Tester:** Qwen Code CLI
- **Channel:** Telegram
- **Knowledge:** Filesystem MCP (local Windows)

## Roadmap

- [x] Phase 0: Thiết kế kiến trúc + knowledge base
- [ ] Phase 1: PraisonAI MVP (Tuần 1–2)
- [ ] Phase 2: Full agent team + stable (Tuần 3–4)
- [ ] Phase 3: CrewAI parallel + benchmark (Tuần 5–6)
- [ ] Phase 4: Optimize + GitHub integration (Tháng 2+)
