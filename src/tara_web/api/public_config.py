"""The deliberately small public configuration endpoint."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(tags=["technical"])


class PublicConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    language: str = Field(pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
    locale: str = Field(pattern=r"^[a-z]{2,3}-[A-Z]{2}$")
    supported_languages: tuple[str, ...]
    max_upload_bytes: int = Field(gt=0)
    recommended_chunk_bytes: int = Field(ge=16_384)
    max_chunk_bytes: int = Field(ge=16_384)
    parallel_uploads: int = Field(ge=1, le=16)


@router.get("/config/public", response_model=PublicConfigResponse)
def public_config(request: Request) -> PublicConfigResponse:
    return PublicConfigResponse.model_validate(request.app.state.public_config)
