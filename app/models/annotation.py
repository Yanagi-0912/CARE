from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class SaveAnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision: int = Field(ge=0)
    label: Annotated[int, Field(ge=0, le=1)] | None
    unsure: bool = False
    note: str = Field(default="", max_length=2000)
    ms: int = Field(ge=0, le=86_400_000)
