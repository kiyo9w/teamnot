from __future__ import annotations

from pathlib import Path

from teamnot.customer_loop import (
    CustomerEvidence,
    CustomerFinding,
    CustomerJob,
    CustomerProfile,
    CustomerReport,
    CustomerScores,
    CustomerSeverity,
    CustomerTestPlan,
    CustomerTestTask,
    ExperienceTarget,
    load_model,
    save_yaml,
)


def _profile() -> CustomerProfile:
    return CustomerProfile(
        persona="Shopify agency operations lead",
        role="operations lead",
        seniority="senior",
        domain_literacy="high",
        current_workflow="Spreadsheet preflight before client imports",
        buying_trigger="Repeated CSV import failures",
        alternatives=["manual spreadsheet QA"],
        buyer_user_split="Buyer and user overlap",
        trust_threshold="Must not misclassify unchecked URLs as failures",
    )


def test_customer_loop_models_round_trip_yaml(tmp_path: Path):
    profile = _profile()
    path = save_yaml(profile, tmp_path / "profile.yaml")
    loaded = load_model(path, CustomerProfile)
    assert loaded.persona == profile.persona
    assert loaded.alternatives == ["manual spreadsheet QA"]


def test_severity_and_scores_serialize_stably():
    finding = CustomerFinding(
        id="f1",
        title="Blank report preview after success",
        severity=CustomerSeverity.high,
        evidence=[CustomerEvidence(path=".teamnot/customer-testing/report.md")],
    )
    scores = CustomerScores(trust_readiness=3, task_success=4)
    dumped = finding.model_dump(mode="json")
    assert dumped["severity"] == "high"
    assert scores.model_dump()["trust_readiness"] == 3


def test_customer_report_is_json_serializable():
    target = ExperienceTarget(url="https://example-product.test")
    plan = CustomerTestPlan(
        target=target,
        customer_job=CustomerJob(functional="Preflight a Shopify CSV"),
        tasks=[CustomerTestTask(id="t1", title="Upload CSV")],
    )
    report = CustomerReport(profile=_profile(), target=target, plan=plan)
    dumped = report.model_dump(mode="json")
    assert dumped["target"]["url"] == "https://example-product.test"
    assert dumped["scores"]["technical_reliability"] == 5
