"""Run Alembic migrations programmatically (used at startup and by `make migrate`)."""

from __future__ import annotations

import asyncio

from alembic import command
from alembic.config import Config

from app.config import ROOT_DIR


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT_DIR / "app" / "db" / "migrations"))
    cfg.attributes["database_url"] = database_url
    return cfg


async def upgrade(database_url: str) -> None:
    # env.py runs its own event loop, so execute it in a worker thread.
    await asyncio.to_thread(command.upgrade, alembic_config(database_url), "head")
