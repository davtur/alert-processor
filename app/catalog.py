"""Chat-model catalog.

The file named by MODELS_FILE is the source of truth. When that variable is
empty, one model is built from the XAI_* environment variables so local runs
and tests keep working.
"""

from __future__ import annotations

import logging
import os
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app import config, db

log = logging.getLogger("alert-processor.catalog")

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_tool_limit: ContextVar[int | None] = ContextVar("tool_result_max_chars", default=None)


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    model: str
    api_url: str
    api_key_env: str = ""
    default: bool = False
    tool_result_max_chars: int | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)

    @property
    def api_key(self) -> str:
        if not self.api_key_env:
            return ""
        return os.environ.get(self.api_key_env, "").strip()


def tool_result_max_chars() -> int:
    override = _tool_limit.get()
    if override and override > 0:
        return override
    return config.TOOL_RESULT_MAX_CHARS


def push_tool_limit(value: int | None) -> Token[int | None]:
    return _tool_limit.set(value)


def pop_tool_limit(token: Token[int | None]) -> None:
    _tool_limit.reset(token)


def _env_model() -> ModelSpec:
    return ModelSpec(
        id="grok",
        label="Grok",
        model=config.XAI_MODEL or "grok",
        api_url=config.XAI_API_URL,
        api_key_env="XAI_API_KEY",
        default=True,
    )


def _field(raw: dict[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        if name in raw and raw[name] is not None:
            return raw[name]
    return default


def _parse_model(raw: Any) -> ModelSpec:
    if not isinstance(raw, dict):
        raise CatalogError("each model must be a mapping")
    model_id = str(_field(raw, "id")).strip()
    if not _ID.fullmatch(model_id):
        raise CatalogError(f"invalid model id {model_id!r}")
    label = str(_field(raw, "label", default=model_id)).strip() or model_id
    model = str(_field(raw, "model")).strip()
    api_url = str(_field(raw, "apiUrl", "api_url")).strip()
    if not model:
        raise CatalogError(f"model {model_id} is missing model")
    if not api_url.startswith(("http://", "https://")):
        raise CatalogError(f"model {model_id} apiUrl must be http or https")
    limit_raw = _field(raw, "toolResultMaxChars", "tool_result_max_chars", default=None)
    limit: int | None = None
    if limit_raw not in (None, ""):
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError) as exc:
            raise CatalogError(f"model {model_id} toolResultMaxChars must be an integer") from exc
        if limit < 200:
            raise CatalogError(f"model {model_id} toolResultMaxChars must be at least 200")
    extra = _field(raw, "extraBody", "extra_body", default={}) or {}
    if not isinstance(extra, dict):
        raise CatalogError(f"model {model_id} extraBody must be a mapping")
    return ModelSpec(
        id=model_id,
        label=label,
        model=model,
        api_url=api_url,
        api_key_env=str(_field(raw, "apiKeyEnv", "api_key_env")).strip(),
        default=bool(_field(raw, "default", default=False)),
        tool_result_max_chars=limit,
        extra_body=dict(extra),
    )


def list_models() -> list[ModelSpec]:
    path = config.MODELS_FILE.strip()
    if not path:
        return [_env_model()]
    file = Path(path)
    if not file.is_file():
        raise CatalogError(f"MODELS_FILE not found: {file}")
    loaded = yaml.safe_load(file.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or not isinstance(loaded.get("models"), list):
        raise CatalogError("MODELS_FILE must be a mapping with a models list")
    specs: list[ModelSpec] = []
    seen: set[str] = set()
    for item in loaded["models"]:
        spec = _parse_model(item)
        if spec.id in seen:
            raise CatalogError(f"duplicate model id {spec.id}")
        seen.add(spec.id)
        specs.append(spec)
    if not specs:
        raise CatalogError("MODELS_FILE models list is empty")
    return specs


def selected() -> ModelSpec:
    models = list_models()
    chosen = ""
    try:
        chosen = db.get_setting("selected_model") or ""
    except Exception:
        log.warning("selected model setting is unavailable", exc_info=True)
    by_id = {model.id: model for model in models}
    if chosen in by_id:
        return by_id[chosen]
    for model in models:
        if model.default:
            return model
    return models[0]


def select(model_id: str) -> ModelSpec:
    models = list_models()
    match = next((model for model in models if model.id == model_id), None)
    if match is None:
        known = ", ".join(model.id for model in models)
        raise CatalogError(f"unknown model {model_id!r}; choose one of: {known}")
    db.set_setting("selected_model", match.id)
    return match


def public_state() -> dict[str, Any]:
    models = list_models()
    current = selected()
    return {
        "selected": current.id,
        "models": [{"id": model.id, "label": model.label} for model in models],
    }
