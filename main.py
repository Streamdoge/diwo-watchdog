from __future__ import annotations

import asyncio
import logging
import os
import time

import pytz
from aiogram import Bot
from dotenv import load_dotenv

import db
import poller
import widgets as wg
from bot import create_dispatcher, alert_snooze_keyboard

USER_LEVEL_SOURCE = wg.USER_LEVEL_SOURCE

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("aiogram").setLevel(logging.WARNING)

logger = logging.getLogger("main")


# ---------------------------------------------------------------------------
# Scheduler: daily summary + snapshot cleanup
# ---------------------------------------------------------------------------

_summary_sent: dict[int, str] = {}  # user_id → "HH:MM" last sent


async def run_scheduler(bot: Bot) -> None:
    logger.info("Планировщик запущен")
    import datetime as dt
    while True:
        await asyncio.sleep(30)
        try:
            now_ts = time.time()
            users = await db.get_all_users()
            for user in users:
                # Проверяем, включён ли виджет "Утренняя сводка"
                ds = await db.get_user_widget(user["tg_id"], wg.USER_LEVEL_SOURCE, wg.WIDGET_DAILY_SUMMARY)
                if not ds or not ds["is_enabled"]:
                    continue
                if ds.get("snoozed_until") and ds["snoozed_until"] > now_ts:
                    continue
                try:
                    tz = pytz.timezone(user["timezone"])
                except Exception:
                    tz = pytz.timezone("Europe/Moscow")
                local_now = dt.datetime.now(tz)
                current_hhmm = local_now.strftime("%H:%M")
                if current_hhmm == user["summary_time"]:
                    last = _summary_sent.get(user["tg_id"])
                    if last != current_hhmm:
                        _summary_sent[user["tg_id"]] = current_hhmm
                        await _send_daily_summary(bot, user["tg_id"])

            # Cleanup snapshots at 00:00 MSK
            msk = pytz.timezone("Europe/Moscow")
            import datetime as dt2
            msk_now = dt2.datetime.now(msk)
            if msk_now.hour == 0 and msk_now.minute == 0:
                await db.cleanup_old_snapshots()
                logger.info("Очистка снапшотов выполнена")

        except Exception as exc:
            logger.error("Ошибка планировщика: %s", exc)


async def _send_daily_summary(bot: Bot, user_id: int) -> None:
    sources = await db.get_user_sources(user_id)
    active = [s for s in sources if s["is_active"]]
    if not active:
        return

    lines = ["📊 Утренняя сводка"]
    for s in active:
        snap = await db.get_latest_snapshot(s["id"])
        if snap:
            offline = snap["total"] - snap["online"]
            lines.append(
                f'Объект "{s["name"]}": Радаров всего {snap["total"]}, '
                f'online {snap["online"]}, offline {offline}'
            )
        else:
            lines.append(f'Объект "{s["name"]}": нет данных')

    try:
        await bot.send_message(user_id, "\n".join(lines))
    except Exception as exc:
        logger.error("Не удалось отправить сводку user=%s: %s", user_id, exc)


# ---------------------------------------------------------------------------
# Alert sender (passed to widgets.analyze)
# ---------------------------------------------------------------------------

def make_alert_sender(bot: Bot):
    async def send_alert(user_id: int, text: str) -> None:
        # Определяем source_id и widget_type из текста не получится —
        # поэтому передаём через замыкание в analyze
        await bot.send_message(user_id, text)
    return send_alert


def make_alert_sender_with_keyboard(bot: Bot):
    """Returns sender that attaches snooze keyboard for sharp_drop alerts."""
    async def send_alert_kb(user_id: int, text: str, source_id: int, widget_type: str) -> None:
        kb = alert_snooze_keyboard(source_id, widget_type)
        try:
            await bot.send_message(user_id, text, reply_markup=kb)
        except Exception as exc:
            logger.error("Не удалось отправить алерт user=%s: %s", user_id, exc)
    return send_alert_kb


# ---------------------------------------------------------------------------
# Snapshot callback — called after each successful poll
# ---------------------------------------------------------------------------

def make_on_snapshot(bot: Bot):
    alert_sender = make_alert_sender_with_keyboard(bot)

    async def on_snapshot(source_id: int, source_name: str, total: int, online: int) -> None:
        async def send_alert_wrapper(user_id: int, text: str) -> None:
            await alert_sender(user_id, text, source_id, wg.WIDGET_SHARP_DROP)

        await wg.analyze(source_id, source_name, total, online, send_alert_wrapper)

    return on_snapshot


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    await db.init_db()
    logger.info("БД инициализирована")

    bot = Bot(token=BOT_TOKEN)
    dp = create_dispatcher()

    on_snapshot = make_on_snapshot(bot)

    await asyncio.gather(
        dp.start_polling(bot),
        poller.run_poller(on_snapshot=on_snapshot),
        run_scheduler(bot),
    )


if __name__ == "__main__":
    asyncio.run(main())
