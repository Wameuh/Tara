"""Stable, deliberately non-diagnostic RFC 9457 responses."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from tara_web.api.schemas import ApiProblemCode, ProblemDetails

_STATUS_CODES: dict[int, ApiProblemCode | str] = {
    400: ApiProblemCode.BAD_REQUEST,
    403: ApiProblemCode.FORBIDDEN,
    404: ApiProblemCode.RESOURCE_UNAVAILABLE,
    405: ApiProblemCode.METHOD_NOT_ALLOWED,
    409: ApiProblemCode.CONFLICT,
    422: "input_invalid",
    428: ApiProblemCode.PRECONDITION_REQUIRED,
    429: ApiProblemCode.RATE_LIMITED,
    500: ApiProblemCode.INTERNAL_ERROR,
}


def problem(request: Request, status: int, code: str | None = None) -> JSONResponse:
    """Do not let exception text, identifiers, or secrets cross this boundary."""
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    stable_code = code or str(_STATUS_CODES.get(status, ApiProblemCode.INTERNAL_ERROR))
    payload = ProblemDetails.model_validate(
        {
            "type": f"https://tara.invalid/problems/{stable_code}",
            "title": "Request could not be completed",
            "status": status,
            "code": stable_code,
            "correlation_id": correlation_id,
        }
    ).model_dump(mode="json")
    return JSONResponse(
        payload,
        status_code=status,
        media_type="application/problem+json",
        headers={"Cache-Control": "no-store"},
    )


def problem_responses(*statuses: int) -> dict[int, dict[str, object]]:
    """Reusable OpenAPI declarations for the uniform protected error surface."""
    return {
        status: {
            "model": ProblemDetails,
            "description": "Uniform protected API error",
            "content": {
                "application/problem+json": {
                    "schema": {"$ref": "#/components/schemas/ProblemDetails"}
                }
            },
        }
        for status in statuses
    }
