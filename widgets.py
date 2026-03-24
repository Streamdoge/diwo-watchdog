from __future__ import annotations

import logging
import time
from typing import Any, Callable, Awaitable

import db

logger = logging.getLogger("widgets")

WIDGET_SHARP_DROP = "sharp_drop"
WIDGET_DAILY_SUMMARY = "daily_summary"
USER_LEVEL_SOURCE = 0  # source_id для виджетов уровня пользователя

# In-memory state: source_id → True if alert is currently active
_alert_active: dict[int, bool] = {}


async def analyze(
    source_id: int,
    source_name: str,
    total: int,
    online: int,
    send_alert: Callable[[int, str], Awaitable[None]],
) -> None:
    """
    Analyze snapshot for all enabled widgets.
    send_alert(user_id, text) — coroutine to send message to user.
    """
    await _check_sharp_drop(source_id, source_name, total, online, send_alert)


async def _check_sharp_drop(
    source_id: int,
    source_name: str,
    total: int,
    online: int,
    send_alert: Callable[[int, str], Awaitable[None]],
) -> None:
    now_ts = int(time.time())
    prev_ts = now_ts - 10 * 60  # 10 минут назад

    prev = await db.get_snapshot_around(source_id, prev_ts, window=120)
    if prev is None:
        return

    prev_online = prev["online"]
    currently_active = _alert_active.get(source_id, False)

    # Проверяем условие аномалии
    is_anomaly = prev_online > 0 and online < 0.5 * prev_online

    if is_anomaly and not currently_active:
        # Новая аномалия — отправляем алерт
        _alert_active[source_id] = True
        subscribers = await db.get_widget_subscribers(source_id, WIDGET_SHARP_DROP)
        if not subscribers:
            return

        text = (
            f"⚠️ Аномалия: Резкое падение\n"
            f"Источник: {source_name}\n"
            f"Было: {prev_online} радаров online\n"
            f"Стало: {online} радаров online"
        )
        for sub in subscribers:
            try:
                await send_alert(sub["user_id"], text)
            except Exception as exc:
                logger.error("Не удалось отправить алерт user=%s: %s", sub["user_id"], exc)

    elif not is_anomaly and currently_active:
        # Аномалия устранена — сбрасываем флаг
        _alert_active[source_id] = False
        logger.info("Аномалия устранена | source=%s (%s)", source_id, source_name)
