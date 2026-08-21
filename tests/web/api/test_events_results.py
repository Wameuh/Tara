from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tara_web.api.routes.events import stream_job_events
from tara_web.app import create_app
from tara_web.config import RuntimeConfig, StorageConfig, WebinterfaceConfig
from tara_web.storage.artifacts import ArtifactService

CURRENT_PUBLIC_RESULT = b"""schema_name: tara.public_result
schema_version: 26.0.1
content:
  title: Public result
  summary_markdown: |-
    # Session summary

    ## Public result

    Opening paragraph.
  sections:
    - section_id: overview-public-result
      section_type: overview
      title: Public result
      blocks:
        - type: paragraph
          text: Opening paragraph.
        - type: list
          items: [First item, Second item]
        - type: orderedList
          items: [First step, Second step]
        - type: keyValue
          entries:
            - key: Status
              value: Ready
        - type: table
          headers: [Name, State]
          rows:
            - [Tara, Ready]
        - type: callout
          title: Note
          text: Keep this in mind.
"""


def _app(tmp_path: Path) -> FastAPI:
    return create_app(
        RuntimeConfig(
            web=WebinterfaceConfig(
                storage=StorageConfig(
                    root=tmp_path / "runtime",
                    backups_root=tmp_path / "backups",
                    sqlite_path=tmp_path / "runtime" / "tara.sqlite3",
                )
            )
        )
    )


def _completed_job(
    client: TestClient, final_yaml: bytes = CURRENT_PUBLIC_RESULT
) -> tuple[str, str, int]:
    created = client.post(
        "/api/v1/uploads/sessions", headers={"Idempotency-Key": "result-session"}
    ).json()
    job_id = "job_result_0000000001"
    now = datetime.now(UTC)
    with client.app.state.database.transaction() as connection:
        session = connection.execute(
            "SELECT id,secret_hmac FROM upload_sessions WHERE public_id=?",
            (created["session_id"],),
        ).fetchone()
        cursor = connection.execute(
            "INSERT INTO jobs(public_id,upload_session_id,secret_hmac,status,"
            "pipeline_version,language,created_at,updated_at,finished_at,expires_at) "
            "VALUES(?,?,?,'completed','v1','fr',?,?,?,?)",
            (
                job_id,
                session["id"],
                session["secret_hmac"],
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                (now + timedelta(days=7)).isoformat(),
            ),
        )
        database_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO job_attempts(job_id,attempt_number,status,finished_at) "
            "VALUES(?,1,'completed',?)",
            (database_id, now.isoformat()),
        )
    ArtifactService(
        client.app.state.storage_layout,
        client.app.state.artifact_repository,
        client.app.state.artifact_policy,
    ).write(
        job_id=database_id,
        artifact_type="final_yaml",
        retention_kind="final_result",
        chunks=(final_yaml,),
    )
    return job_id, created["secret"], database_id


def test_result_projection_and_explicit_cost_states(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        job_id, secret, database_id = _completed_job(client)
        headers = {"X-Tara-Job-Secret": secret}
        result = client.get(f"/api/v1/jobs/{job_id}/result", headers=headers)
        assert result.status_code == 200
        section = result.json()["sections"][0]
        assert section["id"] == "overview-public-result"
        assert section["order"] == 0
        assert section["section_type"] == "overview"
        assert section["title"] == "Public result"
        assert [block["type"] for block in section["blocks"]] == [
            "paragraph",
            "list",
            "orderedList",
            "keyValue",
            "table",
            "callout",
        ]
        assert section["text"] == (
            "Opening paragraph.\nFirst item\nSecond item\nFirst step\nSecond step\n"
            "Status: Ready\nName | State\nTara | Ready\nNote\nKeep this in mind."
        )
        assert result.json()["summary_markdown"] == (
            "# Session summary\n\n## Public result\n\nOpening paragraph."
        )
        assert result.json()["cost"] == {
            "status": "unavailable",
            "value_micro_eur": None,
            "explanation_key": "result.cost_unavailable_explanation",
        }
        assert "schema_version" not in result.text

        with client.app.state.database.transaction() as connection:
            connection.execute(
                "UPDATE job_attempts SET provider_cost_micro_eur=0,cost_known=1 "
                "WHERE job_id=? AND attempt_number=1",
                (database_id,),
            )
            connection.execute(
                "INSERT INTO job_attempts(job_id,attempt_number,status) "
                "VALUES(?,2,'failed')",
                (database_id,),
            )
        assert client.get(f"/api/v1/jobs/{job_id}/result", headers=headers).json()[
            "cost"
        ] == {
            "status": "partial",
            "value_micro_eur": 0,
            "explanation_key": "result.cost_partial_explanation",
        }
        with client.app.state.database.transaction() as connection:
            connection.execute(
                "UPDATE job_attempts SET provider_cost_micro_eur=123,cost_known=1 "
                "WHERE job_id=? AND attempt_number=2",
                (database_id,),
            )
        assert client.get(f"/api/v1/jobs/{job_id}/result", headers=headers).json()[
            "cost"
        ] == {
            "status": "complete",
            "value_micro_eur": 123,
            "explanation_key": "result.cost_explanation",
        }


def test_result_projection_adapts_explicit_final_v1(tmp_path: Path) -> None:
    final_v1 = b"""schema_version: 1
summary:
  title: Legacy public result
  scenes: []
"""
    with TestClient(_app(tmp_path)) as client:
        job_id, secret, _ = _completed_job(client, final_v1)
        result = client.get(
            f"/api/v1/jobs/{job_id}/result", headers={"X-Tara-Job-Secret": secret}
        )
    assert result.status_code == 200
    assert result.json()["sections"][0]["id"] == "overview-legacy-public-result"
    assert result.json()["sections"][0]["section_type"] == "overview"
    assert result.json()["sections"][0]["blocks"] == [
        {"type": "paragraph", "text": "Legacy public result"}
    ]


class _StreamRequest:
    def __init__(self, state: object) -> None:
        self.app = SimpleNamespace(state=state)

    async def is_disconnected(self) -> bool:
        return False


def test_sse_initial_event_heartbeat_and_rotation_revocation(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        job_id, secret, _ = _completed_job(client)
        state = SimpleNamespace(
            database=client.app.state.database,
            secret_hmac=client.app.state.secret_hmac,
            event_broker=client.app.state.event_broker,
            runtime_config=SimpleNamespace(
                web=SimpleNamespace(limits=SimpleNamespace(sse_heartbeat_seconds=0.001))
            ),
        )
        request = _StreamRequest(state)

        async def scenario() -> None:
            queue = state.event_broker.subscribe(job_id)
            stream = stream_job_events(request, job_id, secret, queue)
            first = await anext(stream)
            payload = json.loads(first.split(b"data: ", 1)[1])
            assert payload["type"] == "snapshot_updated"
            assert (await anext(stream)) == b": heartbeat\n\n"
            with state.database.transaction() as connection:
                connection.execute(
                    "UPDATE jobs SET secret_hmac=?,secret_generation="
                    "secret_generation+1,revision=revision+1 WHERE public_id=?",
                    (
                        state.secret_hmac.digest("replacement", "upload-secret"),
                        job_id,
                    ),
                )
            state.event_broker.publish(
                job_id,
                {
                    "type": "snapshot_updated",
                    "revision": payload["revision"] + 1,
                    "data": {},
                },
            )
            with pytest.raises(StopAsyncIteration):
                await anext(stream)
            assert job_id not in state.event_broker._queues

        asyncio.run(scenario())
