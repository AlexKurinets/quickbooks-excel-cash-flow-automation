"""Settings, all from the environment so the same image runs locally, in CI and in Compose."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

HOME = Path(os.environ.get("CASHFLOW_HOME", Path(__file__).resolve().parents[2]))


@dataclass(frozen=True)
class Settings:
    dsn: str
    template: Path
    template_map: Path
    source: str          # "synthetic" or "intuit"
    suggester: str       # "rules" or "openai"
    openai_model: str
    intuit_environment: str
    intuit_client_id: str
    intuit_client_secret: str


def settings() -> Settings:
    env = os.environ.get
    return Settings(
        dsn=env("CASHFLOW_DSN", "postgresql://cashflow:cashflow@localhost:5432/cashflow"),
        template=Path(env("CASHFLOW_TEMPLATE", HOME / "templates" / "monthly-cash-flow.xlsx")),
        template_map=Path(env("CASHFLOW_TEMPLATE_MAP", HOME / "templates" / "monthly-cash-flow.map.yml")),
        source=env("CASHFLOW_SOURCE", "synthetic"),
        suggester=env("CASHFLOW_SUGGESTER", "rules"),
        openai_model=env("CASHFLOW_OPENAI_MODEL", "gpt-5-mini"),
        intuit_environment=env("INTUIT_ENVIRONMENT", "sandbox"),
        intuit_client_id=env("INTUIT_CLIENT_ID", ""),
        intuit_client_secret=env("INTUIT_CLIENT_SECRET", ""),
    )
