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
from teamnot.customer_loop.models import (
    BrowserRuntimeMetadata,
    DomainOutputOracle,
    IterationCoverage,
    JTBDForces,
    PersonaLensResult,
    ResearchActionMemory,
    ScreenshotCaptureRecord,
    SeededCookie,
    SeededCustomerState,
    SeededLocalStorageEntry,
    SeededTestAccount,
    VisionReviewArtifact,
    VisionScreenshotGroup,
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


def test_seeded_state_redacts_sensitive_values():
    state = SeededCustomerState(
        cookies=[SeededCookie(name="session", value="secret", domain="example.test")],
        local_storage=[SeededLocalStorageEntry(origin="https://example.test", values={"token": "secret"})],
        test_account=SeededTestAccount(email="customer@example.test", password="secret"),
        safety_constraints=["Do not touch production data."],
    )

    redacted = state.redacted()

    assert redacted["cookies"][0]["value"] == "***REDACTED***"
    assert redacted["local_storage"][0]["values"]["token"] == "***REDACTED***"
    assert redacted["test_account"]["password"] == "***REDACTED***"


def test_customer_research_schemas_serialize():
    capture = ScreenshotCaptureRecord(path="screenshots/a.png", route="/app", action="run", success=True)
    review = VisionReviewArtifact(
        groups=[VisionScreenshotGroup(group_id="/app:run", screenshots=[capture])],
        screenshot_count=1,
    )
    payload = {
        "runtime": BrowserRuntimeMetadata(cdp_url="http://127.0.0.1:18801", cdp_port=18801),
        "capture": capture,
        "review": review,
        "persona": PersonaLensResult(lens="daily_user", blockers=["Needs output"]),
        "jtbd": JTBDForces(push="manual work", pull="automation"),
        "oracle": DomainOutputOracle(name="Report", expected_output="summary"),
        "memory": ResearchActionMemory(route="/app", chosen_action="run", no_op=False),
        "coverage": IterationCoverage(iteration=2, new_evidence=True),
    }

    assert payload["runtime"].model_dump(mode="json")["cdp_port"] == 18801
    assert payload["review"].model_dump(mode="json")["screenshot_count"] == 1
