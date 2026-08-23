from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

class UsageEvent(BaseModel):
    event_id: str
    provider: Literal["claude", "codex", "gemini"]
    session_id: str
    project_hash: str
    timestamp: datetime
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)
    turn_index: int = Field(default=1, ge=1)
    tool_call_count: int = Field(default=0, ge=0)
    has_error: bool = False
    error_fingerprint_hash: str | None = None
