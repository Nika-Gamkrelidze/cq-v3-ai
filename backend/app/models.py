from datetime import datetime

from pydantic import BaseModel


class CallIngest(BaseModel):
    client_slug: str
    external_ref: str
    audio_uri: str
    operator_external_ref: str | None = None
    operator_name: str | None = None
    language: str | None = None
    duration_sec: int | None = None
    recorded_at: datetime | None = None


class CallOut(BaseModel):
    id: str
    external_ref: str
    status: str
    created: bool
