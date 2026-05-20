"""Application configuration.

All env-var reads go through Settings.from_env() so the rest of the code
never touches os.getenv directly. The `settings` module-level object is
the singleton everyone imports.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


@dataclass(frozen=True)
class Settings:
    # ---- auth ----
    app_password: str
    admin_password: str

    # ---- scheduler ----
    auto_predict_enabled: bool
    auto_predict_interval_seconds: int
    auto_predict_window_minutes: int

    # ---- Anthropic / Claude API ----
    anthropic_api_key: str
    anthropic_model: str

    # ---- data paths ----
    data_dir: str
    predictions_db_path: str
    visitors_db_path: str
    weights_path: str
    backtest_cache_path: str
    salaries_cache_path: str
    tuning_history_path: str

    @property
    def has_anthropic_key(self) -> bool:
        """True when an Anthropic API key is configured."""
        return bool(self.anthropic_api_key)

    @classmethod
    def from_env(cls) -> "Settings":
        app_pw = os.getenv("APP_PASSWORD", "changeme")
        admin_pw = os.getenv("ADMIN_PASSWORD") or (app_pw + "-admin")
        data_dir = os.getenv("DATA_DIR", "data")
        return cls(
            app_password=app_pw,
            admin_password=admin_pw,
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip(),
            auto_predict_enabled=_bool_env("AUTO_PREDICT_ENABLED", True),
            auto_predict_interval_seconds=int(os.getenv("AUTO_PREDICT_INTERVAL_SECONDS", 2 * 60 * 60)),
            auto_predict_window_minutes=int(os.getenv("AUTO_PREDICT_WINDOW_MIN", 150)),
            data_dir=data_dir,
            predictions_db_path=os.path.join(data_dir, "predictions.db"),
            visitors_db_path=os.path.join(data_dir, "visitors.db"),
            weights_path=os.path.join(data_dir, "weights.json"),
            backtest_cache_path=os.path.join(data_dir, "backtest_cache.json"),
            salaries_cache_path=os.path.join(data_dir, "salaries.json"),
            tuning_history_path=os.path.join(data_dir, "tuning_history.json"),
        )


# Load .env once if python-dotenv is installed; harmless if it isn't.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except Exception:
    pass


settings = Settings.from_env()
