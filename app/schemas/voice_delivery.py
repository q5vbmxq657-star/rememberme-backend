from pydantic import BaseModel, ConfigDict, Field


class VoiceDelivery(BaseModel):
    """Bounded delivery preferences; provider voice identity remains server-owned."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    energy: float = Field(default=0.5, ge=0, le=1)
    speaking_speed: float = Field(default=0.5, ge=0, le=1)
    pause_length: float = Field(default=0.5, ge=0, le=1)
