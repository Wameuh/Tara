from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tara_web.db.connection import ConnectionFactory
from tara_web.db.migrations import migrate
from tara_web.estimation.features import EstimationFeatures
from tara_web.estimation.regression import LinearPrediction, fit_linear
from tara_web.estimation.service import EstimationService


def test_linear_regression_recovers_known_coefficients() -> None:
    prediction = fit_linear(
        ((x, 2_000 + 3 * x) for x in range(1, 11)),
        20,
        minimum_observations=8,
    )
    assert prediction is not None
    assert prediction.value == 2_060
    assert prediction.intercept == 2_000
    assert prediction.slope == 3
    assert prediction.confidence == 1


def test_regression_requires_history_and_bounds_negative_slope() -> None:
    assert fit_linear([(1, 2)], 2, minimum_observations=2) is None
    prediction = fit_linear([(1, 9), (2, 7), (3, 5)], 100, minimum_observations=3)
    assert prediction is not None
    assert prediction.slope == 0
    assert prediction.value >= 0


def test_regression_rejects_non_finite_or_hostile_values() -> None:
    assert fit_linear([(1, 2), (-1, 3)], 2, minimum_observations=2) is None
    assert fit_linear([(1, 2), (2, 3)], -1, minimum_observations=2) is None


def test_estimation_service_hides_predictions_below_the_sample_threshold(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    database = ConnectionFactory(root / "tara.sqlite3", root)
    connection = database.connect()
    try:
        migrate(connection)
        for index in range(3):
            connection.execute(
                "INSERT INTO job_metrics(job_public_id,job_type,pipeline_version,"
                "created_at,completed_at,audio_duration_ms,duration_ms,attempt_count) "
                "VALUES(?, 'audio','v1',?,?,?, ?,1)",
                (
                    f"job_metric_000000{index}",
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:01:00+00:00",
                    (index + 1) * 1_000,
                    2_000 + (index + 1) * 2,
                ),
            )
        connection.commit()
    finally:
        connection.close()
    features = EstimationFeatures("audio", "v1", audio_duration_ms=4_000)
    unavailable = EstimationService(
        database, minimum_observations=4
    ).predict_total(features)
    assert unavailable is None
    prediction = EstimationService(
        database, minimum_observations=3
    ).predict_total(features)
    assert prediction is not None and prediction.value == 2_008
    stage = EstimationService(database, minimum_observations=3).predict_stage(
        features, "transcription"
    )
    assert stage is not None and stage.value == 0


def test_running_estimate_uses_current_stage_progress_and_future_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    service = EstimationService(ConnectionFactory(root / "tara.sqlite3", root))
    stage_values = {
        "narrative_analysis": 1_000,
        "synthesis": 200,
        "verification": 100,
    }

    def predict_stage(
        features: EstimationFeatures,
        stage: str,
    ) -> LinearPrediction | None:
        value = stage_values.get(stage)
        return (
            LinearPrediction(value, 1.0, 5, float(value), 0.0)
            if value is not None
            else None
        )

    monkeypatch.setattr(service, "predict_stage", predict_stage)

    remaining = service._estimate_stage_remaining(
        EstimationFeatures(
            "merged_transcription",
            "v1",
            merged_transcription_tokens=10,
        ),
        "narrative_analysis",
        400,
    )

    assert remaining == 900


def test_public_remaining_estimate_prefers_stage_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    database = ConnectionFactory(root / "tara.sqlite3", root)
    now = datetime.now(UTC)
    migration_connection = database.connect()
    try:
        migrate(migration_connection)
    finally:
        migration_connection.close()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,status,expires_at,"
            "created_at,updated_at) VALUES(?,?,'consumed',?,?,?)",
            (
                "session_estimate_0001",
                "v1:" + "a" * 64,
                (now + timedelta(days=1)).isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO jobs(public_id,upload_session_id,secret_hmac,status,"
            "pipeline_version,job_type,language,expires_at,stage,"
            "stage_progress_milli,created_at,updated_at,started_at) "
            "VALUES(?,1,?,'running','v1','merged_transcription','fr',?,"
            "'narrative_analysis',400,?,?,?)",
            (
                "job_estimate_0000001",
                "v1:" + "a" * 64,
                (now + timedelta(days=1)).isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
    service = EstimationService(database)
    monkeypatch.setattr(
        service,
        "predict_total",
        lambda features: LinearPrediction(50_000, 1.0, 5, 50_000.0, 0.0),
    )
    monkeypatch.setattr(
        service,
        "predict_stage",
        lambda features, stage: LinearPrediction(
            {"narrative_analysis": 1_000, "synthesis": 200, "verification": 100}[
                stage
            ],
            1.0,
            5,
            0.0,
            0.0,
        ),
    )

    assert service.estimate_remaining_ms("job_estimate_0000001") == 900
