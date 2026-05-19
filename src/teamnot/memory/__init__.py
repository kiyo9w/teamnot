"""Project memory + knowledge review.

The Workspace owns the bytes; this package adds reasoning on top:

  * knowledge_review — detect gaps in the brief/project context BEFORE
    starting work, so the user fills them in instead of the agent guessing.
"""
from teamnot.memory.knowledge_review import (
    GapSeverity,
    KnowledgeGap,
    KnowledgeReview,
    review_workspace,
)

__all__ = [
    "GapSeverity",
    "KnowledgeGap",
    "KnowledgeReview",
    "review_workspace",
]
