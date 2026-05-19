"""TeamNoT — Autonomous AI development workforce.

Hand over a project brief (`.teamnot/brief.yaml`) and TeamNoT runs until the
machine-verifiable Definition of Done passes, then delivers a deliverable
(feature branch, PR, files, or tarball) plus a report.

Quick start (library):

    from teamnot import Worker, load_brief

    brief = load_brief(".teamnot/brief.yaml")
    worker = Worker(brief)
    result = worker.run_until_done()
    print(result.summary)

Quick start (CLI):

    teamnot init                                  # scaffold .teamnot/brief.yaml
    teamnot run --brief .teamnot/brief.yaml
    teamnot validate --brief .teamnot/brief.yaml
"""
from teamnot.brief import (
    Brief,
    BriefValidationError,
    DefinitionOfDone,
    Deliverable,
    DoDCheck,
    ProjectSpec,
    TaskSpec,
    load_brief,
)
from teamnot.dod import DoDEvaluator, DoDResult
from teamnot.engine import Worker, WorkerResult
from teamnot.memory.knowledge_review import (
    GapSeverity,
    KnowledgeGap,
    KnowledgeReview,
    review_workspace,
)
from teamnot.safety import (
    BillingModel,
    BudgetExceededError,
    CostGuard,
    WorkerNotAllowedError,
    WorkerPausedError,
    WorkerTag,
    register_worker,
)
from teamnot.workspace import Workspace

__version__ = "2.0.0a1"

__all__ = [
    "BillingModel",
    "Brief",
    "BriefValidationError",
    "BudgetExceededError",
    "CostGuard",
    "DefinitionOfDone",
    "Deliverable",
    "DoDCheck",
    "DoDEvaluator",
    "DoDResult",
    "GapSeverity",
    "KnowledgeGap",
    "KnowledgeReview",
    "ProjectSpec",
    "TaskSpec",
    "Worker",
    "WorkerNotAllowedError",
    "WorkerPausedError",
    "WorkerResult",
    "WorkerTag",
    "Workspace",
    "load_brief",
    "register_worker",
    "review_workspace",
    "__version__",
]
