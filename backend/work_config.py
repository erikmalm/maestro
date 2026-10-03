"""Saved task models and bounded policy for local background work."""
from pydantic import BaseModel, ConfigDict, Field


class WorkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    reflection_model: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9_./:-]*$")
    memory_model: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9_./:-]*$")
    coding_model: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9_./:-]*$")
    enabled: bool = False
    auto_curate: bool = False
    periodic_reflection: bool = False
    reflection_interval_minutes: int = Field(default=360, ge=30, le=10080)
    debounce_seconds: int = Field(default=60, ge=0, le=3600)
    idle_seconds: int = Field(default=30, ge=0, le=3600)
    max_output_tokens: int = Field(default=512, ge=1, le=1024)
    max_jobs_per_day: int = Field(default=10, ge=0, le=1000)
    max_tokens_per_day: int = Field(default=10000, ge=0, le=10000000)
    timeout_seconds: int = Field(default=180, ge=1, le=1800)
    memory_recall_count: int = Field(default=5, ge=0, le=5)
    memory_recall_characters: int = Field(default=1000, ge=0, le=1000)


def config_value(workspace):
    return WorkConfig.model_validate(workspace.get("work_config", {})).model_dump()


def select_model(config, kind, available_models, chat_model):
    recommendations = {"reflection": "gpt-oss:20b", "memory": "qwen2.5:7b", "coding": "devstral-small-2:24b"}
    if kind not in recommendations:
        raise ValueError("Choose reflection, memory or coding for local work.")
    recommended = recommendations[kind]
    return config[kind + "_model"] or (recommended if recommended in available_models else chat_model)
