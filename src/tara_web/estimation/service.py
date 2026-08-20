"""Read-only historical estimator; fake runs never enter its sample set."""

from __future__ import annotations

from datetime import UTC, datetime

from tara_web.db.connection import ConnectionFactory

from .features import EstimationFeatures
from .regression import LinearPrediction, fit_linear


class EstimationService:
    def __init__(
        self,
        database: ConnectionFactory,
        *,
        minimum_observations: int = 8,
        maximum_prediction_ms: int = 86_400_000,
        active_capacity: int = 1,
    ) -> None:
        if not 2 <= minimum_observations <= 10_000:
            raise ValueError("estimation sample threshold is invalid")
        self._database = database
        self.minimum_observations = minimum_observations
        self.maximum_prediction_ms = maximum_prediction_ms
        if not 1 <= active_capacity <= 32:
            raise ValueError("estimation active capacity is invalid")
        self.active_capacity = active_capacity

    def predict_total(self, features: EstimationFeatures) -> LinearPrediction | None:
        return self._predict(features, "duration_ms")

    def predict_stage(
        self, features: EstimationFeatures, stage: str
    ) -> LinearPrediction | None:
        targets = {
            "input_validation": "validation_duration_ms",
            "transcription": "transcription_duration_ms",
            "session_preparation": "preparation_duration_ms",
            "narrative_analysis": "narrative_analysis_duration_ms",
            "synthesis": "synthesis_duration_ms",
            "verification": "verification_duration_ms",
        }
        target = targets.get(stage)
        return self._predict(features, target) if target else None

    def _predict(
        self, features: EstimationFeatures, target: str | None
    ) -> LinearPrediction | None:
        if target is None:
            return None
        predictor = (
            "audio_duration_ms"
            if features.job_type == "audio"
            else "merged_transcription_tokens"
        )
        connection = self._database.connect()
        try:
            rows = connection.execute(
                f"SELECT {predictor},{target} FROM job_metrics WHERE job_type=? "
                "AND pipeline_version=? ORDER BY completed_at DESC LIMIT 10000",
                (features.job_type, features.pipeline_version),
            ).fetchall()
        finally:
            connection.close()
        return fit_linear(
            ((int(row[0]), int(row[1])) for row in rows),
            features.primary_value,
            minimum_observations=self.minimum_observations,
            maximum_prediction=self.maximum_prediction_ms,
        )

    def estimate_remaining_ms(self, job_public_id: str) -> int | None:
        connection = self._database.connect()
        try:
            row = connection.execute(
                "SELECT j.id,j.job_type,j.pipeline_version,j.status,j.created_at,"
                "j.started_at,"
                "COALESCE(SUM(f.duration_ms),0) AS audio_duration_ms,"
                "COALESCE(SUM(f.token_count),0) AS merged_tokens,"
                "COUNT(f.id) AS input_file_count FROM jobs j LEFT JOIN upload_files f "
                "ON f.job_id=j.id AND f.active=1 WHERE j.public_id=? GROUP BY j.id",
                (job_public_id,),
            ).fetchone()
            ahead = (
                connection.execute(
                    "SELECT j.job_type,j.pipeline_version,"
                    "COALESCE(SUM(f.duration_ms),0) AS audio_duration_ms,"
                    "COALESCE(SUM(f.token_count),0) AS merged_tokens,"
                    "COUNT(f.id) AS input_file_count FROM jobs j LEFT JOIN "
                    "upload_files f ON f.job_id=j.id AND f.active=1 WHERE "
                    "j.status='queued' AND (j.created_at<? OR "
                    "(j.created_at=? AND j.id<?)) GROUP BY j.id ORDER BY "
                    "j.created_at,j.id",
                    (row["created_at"], row["created_at"], row["id"]),
                ).fetchall()
                if row is not None and row["status"] == "queued"
                else ()
            )
        finally:
            connection.close()
        if row is None:
            return None
        prediction = self.predict_total(
            EstimationFeatures(
                str(row["job_type"]),
                str(row["pipeline_version"]),
                int(row["audio_duration_ms"]),
                int(row["merged_tokens"]),
                int(row["input_file_count"]),
            )
        )
        if prediction is None:
            return None
        waiting = 0
        for item in ahead:
            queued_prediction = self.predict_total(
                EstimationFeatures(
                    str(item["job_type"]),
                    str(item["pipeline_version"]),
                    int(item["audio_duration_ms"]),
                    int(item["merged_tokens"]),
                    int(item["input_file_count"]),
                )
            )
            if queued_prediction is None:
                return None
            waiting += queued_prediction.value
        elapsed = 0
        if row["started_at"]:
            try:
                elapsed = max(
                    0,
                    round(
                        (
                            datetime.now(UTC)
                            - datetime.fromisoformat(
                                str(row["started_at"])
                            ).astimezone(UTC)
                        ).total_seconds()
                        * 1000
                    ),
                )
            except ValueError:
                return None
        return max(0, prediction.value - elapsed) + waiting // self.active_capacity
