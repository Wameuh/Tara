"""Shared strict schema primitives."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "26.0.1"
SchemaId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


class PortableMetadata(StrictSchema):
    """Portable provenance only; never paths, prompts, traces, or database IDs."""

    language: Annotated[
        str | None, Field(default=None, pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
    ]
    producer: Annotated[str | None, Field(default=None, max_length=128)]
    created_at: Annotated[str | None, Field(default=None, max_length=64)]
    labels: dict[
        Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")],
        Annotated[str, Field(max_length=256)],
    ] = Field(default_factory=dict, max_length=32)


def dump_schema(model: StrictSchema) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=True)
