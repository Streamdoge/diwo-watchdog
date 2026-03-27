from __future__ import annotations

import aiosqlite
import os
from typing import Any

DB_PATH = os.getenv("DB_PATH", "watchdog.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id        INTEGER PRIMARY KEY,
    timezone     TEXT    NOT NULL DEFAULT 'Europe/Moscow',
    summary_time TEXT    NOT NULL DEFAULT '08:00',
    summary_days TEXT    NOT NULL DEFAULT '0,1,2,3,4,5,6'
);

CREATE TABLE IF NOT EXISTS sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    company_id    INTEGER NOT NULL,
    api_base_url  TEXT    NOT NULL,
    auth_base_url TEXT    NOT NULL,
    login         TEXT    NOT NULL,
    password_enc  TEXT    NOT NULL,
    poll_interval INTEGER NOT NULL DEFAULT 60
);

CREATE TABLE IF NOT EXISTS user_sources (
    user_id   INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, source_id)
);

CREATE TABLE IF NOT EXISTS user_widgets (
    user_id       INTEGER NOT NULL,
    source_id     INTEGER NOT NULL,
    widget_type   TEXT    NOT NULL,
    is_enabled    INTEGER NOT NULL DEFAULT 1,
    snoozed_until INTEGER DEFAULT NULL,
    PRIMARY KEY (user_id, source_id, widget_type)
);

CREATE TABLE IF NOT EXISTS radar_snapshots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    ts        INTEGER NOT NULL,
    total     INTEGER NOT NULL,
    online    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots ON radar_snapshots(source_id, ts);
CREATE INDEX IF NOT EXISTS idx_user_widgets_user ON user_widgets(user_id);
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()
        try:
            await db.execute(
                "ALTER TABLE users ADD COLUMN summary_days TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6'"
            )
            await db.commit()
        except Exception:
            pass  # column already exists


# --- Users ---

async def upsert_user(tg_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (tg_id) VALUES (?)", (tg_id,)
        )
        await db.commit()


async def get_user(tg_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT tg_id, timezone, summary_time, summary_days FROM users WHERE tg_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def set_user_timezone(tg_id: int, timezone: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET timezone = ? WHERE tg_id = ?", (timezone, tg_id)
        )
        await db.commit()


async def set_user_summary_time(tg_id: int, summary_time: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET summary_time = ? WHERE tg_id = ?", (summary_time, tg_id)
        )
        await db.commit()


async def set_user_summary_days(tg_id: int, summary_days: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET summary_days = ? WHERE tg_id = ?", (summary_days, tg_id)
        )
        await db.commit()


async def get_all_users() -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT tg_id, timezone, summary_time, summary_days FROM users") as cur:
            return [dict(r) for r in await cur.fetchall()]


# --- Sources ---

async def add_source(
    name: str,
    company_id: int,
    api_base_url: str,
    auth_base_url: str,
    login: str,
    password_enc: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO sources (name, company_id, api_base_url, auth_base_url, login, password_enc)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, company_id, api_base_url, auth_base_url, login, password_enc),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def get_source(source_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def find_sources_by_credentials(
    company_id: int, api_base_url: str, auth_base_url: str, login: str
) -> list[dict[str, Any]]:
    """Return sources matching company/urls/login (password comparison done in caller)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM sources
               WHERE company_id = ? AND api_base_url = ? AND auth_base_url = ? AND login = ?""",
            (company_id, api_base_url, auth_base_url, login),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_all_active_sources() -> list[dict[str, Any]]:
    """Return sources that have at least one active user_source."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT DISTINCT s.* FROM sources s
               JOIN user_sources us ON us.source_id = s.id
               WHERE us.is_active = 1"""
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def update_source_poll_interval(source_id: int, poll_interval: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sources SET poll_interval = ? WHERE id = ?",
            (poll_interval, source_id),
        )
        await db.commit()


# --- User ↔ Source ---

async def link_user_source(user_id: int, source_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO user_sources (user_id, source_id, is_active) VALUES (?, ?, 1)",
            (user_id, source_id),
        )
        await db.commit()


async def get_user_sources(user_id: int) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT s.id, s.name, s.company_id, us.is_active
               FROM sources s
               JOIN user_sources us ON us.source_id = s.id
               WHERE us.user_id = ?
               ORDER BY s.id""",
            (user_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def set_user_source_active(user_id: int, source_id: int, is_active: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE user_sources SET is_active = ? WHERE user_id = ? AND source_id = ?",
            (1 if is_active else 0, user_id, source_id),
        )
        await db.commit()


async def get_users_for_source(source_id: int) -> list[dict[str, Any]]:
    """Return users subscribed to a source (active)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT u.tg_id, u.timezone, u.summary_time
               FROM users u
               JOIN user_sources us ON us.user_id = u.tg_id
               WHERE us.source_id = ? AND us.is_active = 1""",
            (source_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# --- Widgets ---

async def get_user_widgets(user_id: int) -> list[dict[str, Any]]:
    # JOIN с sources намеренно исключает виджеты с source_id=0 (daily_summary).
    # Для работы с daily_summary используйте get_user_widget(user_id, USER_LEVEL_SOURCE, ...).
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT uw.source_id, s.name as source_name, uw.widget_type, uw.is_enabled, uw.snoozed_until
               FROM user_widgets uw
               JOIN sources s ON s.id = uw.source_id
               WHERE uw.user_id = ?
               ORDER BY uw.source_id, uw.widget_type""",
            (user_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def upsert_widget(user_id: int, source_id: int, widget_type: str, is_enabled: bool = True) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO user_widgets (user_id, source_id, widget_type, is_enabled)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, source_id, widget_type)
               DO UPDATE SET is_enabled = excluded.is_enabled""",
            (user_id, source_id, widget_type, 1 if is_enabled else 0),
        )
        await db.commit()


async def set_widget_enabled(user_id: int, source_id: int, widget_type: str, is_enabled: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE user_widgets SET is_enabled = ?
               WHERE user_id = ? AND source_id = ? AND widget_type = ?""",
            (1 if is_enabled else 0, user_id, source_id, widget_type),
        )
        await db.commit()


async def set_widget_snoozed(user_id: int, source_id: int, widget_type: str, snoozed_until: int | None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE user_widgets SET snoozed_until = ?
               WHERE user_id = ? AND source_id = ? AND widget_type = ?""",
            (snoozed_until, user_id, source_id, widget_type),
        )
        await db.commit()


async def get_user_widget(user_id: int, source_id: int, widget_type: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_widgets WHERE user_id = ? AND source_id = ? AND widget_type = ?",
            (user_id, source_id, widget_type),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def delete_source_for_user(user_id: int, source_id: int) -> None:
    """Remove user's connection to source. Deletes source itself if no other users reference it."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM user_sources WHERE user_id = ? AND source_id = ?", (user_id, source_id))
        await db.execute("DELETE FROM user_widgets WHERE user_id = ? AND source_id = ?", (user_id, source_id))
        await db.commit()
        async with db.execute("SELECT COUNT(*) FROM user_sources WHERE source_id = ?", (source_id,)) as cur:
            row = await cur.fetchone()
            count = row[0] if row else 0
        if count == 0:
            await db.execute("DELETE FROM radar_snapshots WHERE source_id = ?", (source_id,))
            await db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
            await db.commit()


async def get_widget_subscribers(source_id: int, widget_type: str) -> list[dict[str, Any]]:
    """Return users with this widget enabled and not snoozed."""
    import time
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT uw.user_id, uw.snoozed_until
               FROM user_widgets uw
               JOIN user_sources us ON us.user_id = uw.user_id AND us.source_id = uw.source_id
               WHERE uw.source_id = ? AND uw.widget_type = ?
                 AND uw.is_enabled = 1
                 AND us.is_active = 1
                 AND (uw.snoozed_until IS NULL OR uw.snoozed_until < ?)""",
            (source_id, widget_type, now),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# --- Snapshots ---

async def save_snapshot(source_id: int, ts: int, total: int, online: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO radar_snapshots (source_id, ts, total, online) VALUES (?, ?, ?, ?)",
            (source_id, ts, total, online),
        )
        await db.commit()


async def get_snapshot_around(source_id: int, target_ts: int, window: int = 120) -> dict[str, Any] | None:
    """Return snapshot closest to target_ts within ±window seconds."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM radar_snapshots
               WHERE source_id = ? AND ts BETWEEN ? AND ?
               ORDER BY ABS(ts - ?) ASC
               LIMIT 1""",
            (source_id, target_ts - window, target_ts + window, target_ts),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_latest_snapshot(source_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM radar_snapshots WHERE source_id = ? ORDER BY ts DESC LIMIT 1",
            (source_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def cleanup_old_snapshots() -> None:
    """Delete snapshots older than 24 hours."""
    import time
    cutoff = int(time.time()) - 86400
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM radar_snapshots WHERE ts < ?", (cutoff,))
        await db.commit()
