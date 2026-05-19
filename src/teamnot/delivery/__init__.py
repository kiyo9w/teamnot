"""Delivery — what TeamNoT hands back to the user when the pipeline is done.

Four deliverable types are supported (see ``brief.Deliverable.type``):

  * feature_branch — make a git branch, leave changes there for review
  * pull_request   — feature_branch + open a PR via ``gh``
  * files          — list of changed files, no git ops
  * tarball        — same as files, packed into a .tar.gz
  * report_only    — no code handover, just the report

Reports are delivered separately through one of:
  stdout | file | telegram | webhook
"""
from teamnot.delivery.git_branch import (
    GitNotFound,  # backwards-compatible alias
    GitNotFoundError,
    GitState,
    create_feature_branch,
    detect_repo,
    diff_summary,
)
from teamnot.delivery.handover import HandoverResult, handover

__all__ = [
    "GitNotFound",
    "GitNotFoundError",
    "GitState",
    "HandoverResult",
    "create_feature_branch",
    "detect_repo",
    "diff_summary",
    "handover",
]
