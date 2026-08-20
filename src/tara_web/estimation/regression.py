"""Small deterministic univariate regression with bounded nonnegative output."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LinearPrediction:
    value: int
    confidence: float
    observations: int
    intercept: float
    slope: float


def fit_linear(
    samples: Iterable[tuple[int, int]],
    value: int,
    *,
    minimum_observations: int,
    maximum_prediction: int = 86_400_000,
) -> LinearPrediction | None:
    points = tuple(samples)
    if (
        isinstance(value, bool)
        or value < 0
        or not 2 <= minimum_observations <= 100_000
        or not 1 <= maximum_prediction <= 10**15
        or len(points) < minimum_observations
        or any(
            isinstance(x, bool)
            or isinstance(y, bool)
            or not 0 <= x <= 10**15
            or not 0 <= y <= 10**15
            for x, y in points
        )
    ):
        return None
    mean_x = math.fsum(point[0] for point in points) / len(points)
    mean_y = math.fsum(point[1] for point in points) / len(points)
    variance = math.fsum((x - mean_x) ** 2 for x, _ in points)
    slope = (
        math.fsum((x - mean_x) * (y - mean_y) for x, y in points) / variance
        if variance > 0
        else 0.0
    )
    slope = max(0.0, slope)
    intercept = max(0.0, mean_y - slope * mean_x)
    predicted = min(maximum_prediction, max(0, round(intercept + slope * value)))
    total = math.fsum((y - mean_y) ** 2 for _, y in points)
    residual = math.fsum((y - (intercept + slope * x)) ** 2 for x, y in points)
    confidence = 1.0 if total == 0 and residual == 0 else max(0.0, 1 - residual / total)
    if not all(math.isfinite(item) for item in (slope, intercept, confidence)):
        return None
    return LinearPrediction(predicted, confidence, len(points), intercept, slope)
