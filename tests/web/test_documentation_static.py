from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
DOCS = ROOT / "docs" / "webinterface"


def read_document(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def test_v1_required_documents_exist() -> None:
    assert {path.name for path in DOCS.glob("*.md")} >= {
        "api.md",
        "release-checklist.md",
        "runbook.md",
        "v1-validation.md",
    }


def test_runbook_covers_required_operator_procedures() -> None:
    runbook = read_document("runbook.md").lower()
    for topic in (
        "installation",
        "configuration",
        "santé",
        "drain",
        "sauvegarde",
        "migration",
        "restauration",
        "rotation",
        "budget",
        "circuit",
        "disque",
        "provider",
        "artefact corrompu",
        "réponse à incident",
    ):
        assert topic in runbook


def test_api_guide_covers_public_protocol_contracts() -> None:
    api = read_document("api.md")
    for contract in (
        "/api/v1",
        "#secret=",
        "X-Tara-Job-Secret",
        "Idempotency-Key",
        "Expected-Revision",
        "text/event-stream",
        "application/problem+json",
        "schema_version",
    ):
        assert contract in api


def test_release_documents_keep_external_proofs_explicit() -> None:
    checklist = read_document("release-checklist.md").lower()
    report = read_document("v1-validation.md").lower()
    assert "contrôleurs cgroup" in checklist
    assert "docker desktop windows" in checklist
    assert "liste d'exceptions" in checklist
    assert "risques résiduels" in report
    assert "modèle de menace final" in report
    assert "décision finale signée" in report
