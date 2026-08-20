# ruff: noqa: ANN201
"""Public result projection, never raw artifacts or filesystem paths."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, Request

from tara.schemas.registry import load_public_result_text
from tara_web.api.dependencies.auth import job_owner
from tara_web.api.problem_details import problem
from tara_web.api.schemas import ResultSnapshot
from tara_web.services.costs import aggregate_attempt_costs
from tara_web.storage.artifacts import ArtifactService
from tara_web.storage.layout import StorageError

router = APIRouter(prefix="/jobs", tags=["results"])


@router.get("/{job_id}/result", response_model=ResultSnapshot)
def get_result(
    request: Request,
    job_id: str,
    secret: Annotated[str | None, Header(alias="X-Tara-Job-Secret")] = None,
):
    row = job_owner(request, job_id, secret)
    if row is None:
        return problem(request, 404)
    if row["status"] != "completed":
        return problem(request, 409)
    connection = request.app.state.database.connect()
    try:
        artifact = connection.execute(
            "SELECT id,expires_at FROM job_artifacts WHERE job_id=? "
            "AND artifact_type='final_yaml' AND storage_state='ready' "
            "ORDER BY id DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
    finally:
        connection.close()
    if artifact is None:
        return problem(request, 404)
    cost = _result_cost(request, int(row["id"]))
    expires_at = datetime.fromisoformat(str(artifact["expires_at"])).astimezone(UTC)
    if expires_at <= datetime.now(UTC):
        return ResultSnapshot.model_validate(
            {
                "type": "tara_result_v1",
                "status": "expired",
                "expires_at": artifact["expires_at"],
                "sections": (),
                "cost": cost,
            }
        ).model_dump(mode="json")
    try:
        content = ArtifactService(
            request.app.state.storage_layout,
            request.app.state.artifact_repository,
            request.app.state.artifact_policy,
        ).read_final_yaml(artifact_id=int(artifact["id"]), job_id=int(row["id"]))
        document = load_public_result_text(content.decode("utf-8"))
        sections = _public_sections(document)
    except (StorageError, UnicodeError, ValueError):
        return problem(request, 409, "result_integrity_failed")
    return ResultSnapshot.model_validate(
        {
            "type": "tara_result_v1",
            "status": "available",
            "expires_at": artifact["expires_at"],
            "sections": sections,
            "cost": cost,
        }
    ).model_dump(mode="json")


def _result_cost(request: Request, job_id: int) -> dict[str, object]:
    connection = request.app.state.database.connect()
    try:
        attempts = connection.execute(
            "SELECT provider_cost_micro_eur,cost_known,has_known_cost "
            "FROM job_attempts "
            "WHERE job_id=? ORDER BY attempt_number",
            (job_id,),
        ).fetchall()
    finally:
        connection.close()
    return aggregate_attempt_costs(attempts)


def _public_sections(document: object) -> list[dict[str, object]]:
    content = getattr(document, "content", None)
    if content is None:
        raise ValueError("result schema is unsupported")
    return [
        {
            "id": section.section_id,
            "order": index,
            "title": section.title,
            "status": "available",
            "section_type": section.section_type,
            "blocks": [block.model_dump(mode="json") for block in section.blocks],
            "text": "\n".join(_block_text(block) for block in section.blocks)[:100_000],
        }
        for index, section in enumerate(content.sections)
    ]


def _block_text(block: object) -> str:
    if hasattr(block, "title"):
        return f"{block.title}\n{block.text}"
    if hasattr(block, "text"):
        return str(block.text)
    if hasattr(block, "items"):
        return "\n".join(
            f"{item.key}: {item.value}" if hasattr(item, "key") else str(item)
            for item in block.items
        )
    if hasattr(block, "entries"):
        return "\n".join(f"{item.key}: {item.value}" for item in block.entries)
    if hasattr(block, "rows"):
        return "\n".join(
            [" | ".join(block.headers)] + [" | ".join(row) for row in block.rows]
        )
    return ""
