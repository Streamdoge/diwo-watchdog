from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

import db
import poller
import widgets as wg

logger = logging.getLogger("bot")
router = Router()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIMEZONES = [
    ("Москва (UTC+3)", "Europe/Moscow"),
    ("Екатеринбург (UTC+5)", "Asia/Yekaterinburg"),
    ("Омск (UTC+6)", "Asia/Omsk"),
    ("Новосибирск (UTC+7)", "Asia/Novosibirsk"),
    ("Красноярск (UTC+7)", "Asia/Krasnoyarsk"),
    ("Иркутск (UTC+8)", "Asia/Irkutsk"),
    ("Якутск (UTC+9)", "Asia/Yakutsk"),
    ("Владивосток (UTC+10)", "Asia/Vladivostok"),
    ("Магадан (UTC+11)", "Asia/Magadan"),
    ("Камчатка (UTC+12)", "Asia/Kamchatka"),
]
TZ_BY_CODE = {code: label for label, code in TIMEZONES}

WIDGET_LABELS = {
    wg.WIDGET_SHARP_DROP: "Резкое падение",
    wg.WIDGET_DAILY_SUMMARY: "Сводка",
}

# ---------------------------------------------------------------------------
# Reply keyboard — главное меню, 3 кнопки по 1 в ряд
# ---------------------------------------------------------------------------

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📡 Добавить источник")],
        [KeyboardButton(text="📋 Мои источники")],
        [KeyboardButton(text="⚙️ Настройки")],
    ],
    resize_keyboard=True,
)

# ---------------------------------------------------------------------------
# Inline keyboard builders
# ---------------------------------------------------------------------------

def sources_list_kb(sources: list) -> InlineKeyboardMarkup:
    rows = []
    for s in sources:
        status = "✅" if s["is_active"] else "❌"
        rows.append([InlineKeyboardButton(text=f"{status} {s['name']}", callback_data=f"src_open:{s['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def source_detail_kb(source_id: int, is_active: bool, widgets: list) -> InlineKeyboardMarkup:
    rows = []
    active_label = "✅ Источник включён" if is_active else "❌ Источник выключен"
    rows.append([InlineKeyboardButton(text=active_label, callback_data=f"src_toggle:{source_id}")])
    for w in widgets:
        wstatus = "✅" if w["is_enabled"] else "❌"
        label = WIDGET_LABELS.get(w["widget_type"], w["widget_type"])
        rows.append([InlineKeyboardButton(
            text=f"{wstatus} {label}",
            callback_data=f"wgt_toggle:{source_id}:{w['widget_type']}",
        )])
    rows.append([InlineKeyboardButton(text="📊 Сводка сейчас", callback_data=f"src_now:{source_id}")])
    rows.append([
        InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"src_delete:{source_id}"),
        InlineKeyboardButton(text="◀️ Назад", callback_data="src_back"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def delete_confirm_kb(source_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"src_del_ok:{source_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"src_open:{source_id}"),
    ]])


def post_add_widgets_kb(new_source_id: int, states: dict) -> InlineKeyboardMarkup:
    rows = []
    for wtype, enabled in states.items():
        status = "✅" if enabled else "❌"
        label = WIDGET_LABELS.get(wtype, wtype)
        rows.append([InlineKeyboardButton(
            text=f"{status} {label}",
            callback_data=f"padwgt:{new_source_id}:{wtype}",
        )])
    rows.append([InlineKeyboardButton(text="➡️ Готово", callback_data=f"padwgt_done:{new_source_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_inline_kb(tz_label: str, ds_enabled: bool, summary_time: str) -> InlineKeyboardMarkup:
    ds_label = f"📊 Сводка: {summary_time}" if ds_enabled else "📊 Сводка: выкл"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🌍 {tz_label}", callback_data="settings_tz")],
        [InlineKeyboardButton(text=ds_label, callback_data="settings_ds")],
    ])


def tz_inline_kb() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(TIMEZONES), 2):
        row = [InlineKeyboardButton(text=TIMEZONES[i][0], callback_data=f"tz_set:{TIMEZONES[i][1]}")]
        if i + 1 < len(TIMEZONES):
            row.append(InlineKeyboardButton(text=TIMEZONES[i + 1][0], callback_data=f"tz_set:{TIMEZONES[i + 1][1]}"))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def alert_snooze_keyboard(source_id: int, widget_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отключить до завтра", callback_data=f"snooze:{source_id}:{widget_type}:1"),
        InlineKeyboardButton(text="Отключить на неделю", callback_data=f"snooze:{source_id}:{widget_type}:7"),
    ]])


# ---------------------------------------------------------------------------
# FSM States
# ---------------------------------------------------------------------------

class AddSource(StatesGroup):
    name = State()
    company_id = State()
    api_base_url = State()
    auth_base_url = State()
    login = State()
    password = State()


class SetSummaryTime(StatesGroup):
    input = State()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def format_source_summary(name: str, total: int, online: int) -> str:
    offline = total - online
    online_pct = round(online / total * 100) if total else 0
    offline_pct = round(offline / total * 100) if total else 0
    return (
        f'"{name}":\n'
        f"Радаров {total}:\n"
        f"Online {online} / {online_pct}%\n"
        f"Offline {offline} / {offline_pct}%"
    )


def _parse_time(text: str) -> str | None:
    parts = text.strip().split(":")
    if (
        len(parts) == 2
        and parts[0].isdigit() and parts[1].isdigit()
        and 0 <= int(parts[0]) <= 23
        and 0 <= int(parts[1]) <= 59
    ):
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    return None


def _format_interval(seconds: int) -> str:
    if seconds < 3600:
        return f"{seconds // 60} мин"
    if seconds < 86400:
        return f"{seconds // 3600} ч"
    return f"{seconds // 86400} д"


def _clean_error(exc: Exception) -> str:
    msg = str(exc)
    if "\nFor more information" in msg:
        msg = msg.split("\nFor more information")[0]
    return msg


async def _sources_content(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    sources = await db.get_user_sources(user_id)
    text = "📋 Ваши источники:" if sources else "📋 Источников нет."
    return text, sources_list_kb(sources)


async def _source_detail_content(user_id: int, source_id: int) -> tuple[str, InlineKeyboardMarkup]:
    src = await db.get_source(source_id)
    name = src["name"] if src else f"#{source_id}"
    sources = await db.get_user_sources(user_id)
    user_src = next((s for s in sources if s["id"] == source_id), None)
    is_active = bool(user_src and user_src["is_active"])
    all_widgets = await db.get_user_widgets(user_id)
    widgets = [w for w in all_widgets if w["source_id"] == source_id]
    text = f"📡 {name}\n⏱ Опрос: каждые {_format_interval(poller.POLL_INTERVAL)}"
    return text, source_detail_kb(source_id, is_active, widgets)


async def _settings_content(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    user = await db.get_user(user_id)
    tz_code = user["timezone"] if user else "Europe/Moscow"
    tz_label = TZ_BY_CODE.get(tz_code, tz_code)
    st = user["summary_time"] if user else "08:00"
    ds = await db.get_user_widget(user_id, wg.USER_LEVEL_SOURCE, wg.WIDGET_DAILY_SUMMARY)
    ds_enabled = bool(ds and ds["is_enabled"])
    ds_str = f"вкл, {st}" if ds_enabled else "выкл"
    text = f"⚙️ Настройки\nТайм-зона: {tz_label}\nСводка: {ds_str}"
    return text, settings_inline_kb(tz_label, ds_enabled, st)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await db.upsert_user(msg.from_user.id)  # type: ignore[union-attr]
    await msg.answer(
        "Привет! Я Diwo Watchdog — бот мониторинга оборудования DIWO.\n"
        "Добавьте источник данных и настройте виджеты.",
        reply_markup=MAIN_MENU,
    )


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

@router.message(F.text == "📋 Мои источники")
async def my_sources(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await db.upsert_user(msg.from_user.id)  # type: ignore[union-attr]
    text, kb = await _sources_content(msg.from_user.id)  # type: ignore[union-attr]
    await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("src_open:"))
async def cb_src_open(cb: CallbackQuery) -> None:
    source_id = int(cb.data.split(":")[1])  # type: ignore[union-attr]
    await cb.answer()
    text, kb = await _source_detail_content(cb.from_user.id, source_id)
    await cb.message.edit_text(text, reply_markup=kb)  # type: ignore[union-attr]


@router.callback_query(F.data.startswith("src_toggle:"))
async def cb_src_toggle(cb: CallbackQuery) -> None:
    source_id = int(cb.data.split(":")[1])  # type: ignore[union-attr]
    user_id = cb.from_user.id
    sources = await db.get_user_sources(user_id)
    src = next((s for s in sources if s["id"] == source_id), None)
    if not src:
        await cb.answer("Источник не найден")
        return
    new_active = not bool(src["is_active"])
    await db.set_user_source_active(user_id, source_id, new_active)
    await cb.answer("Включён ✅" if new_active else "Выключен ❌")
    text, kb = await _source_detail_content(user_id, source_id)
    await cb.message.edit_text(text, reply_markup=kb)  # type: ignore[union-attr]


@router.callback_query(F.data.startswith("src_delete:"))
async def cb_src_delete(cb: CallbackQuery) -> None:
    source_id = int(cb.data.split(":")[1])  # type: ignore[union-attr]
    sources = await db.get_user_sources(cb.from_user.id)
    src = next((s for s in sources if s["id"] == source_id), None)
    name = src["name"] if src else f"#{source_id}"
    await cb.answer()
    await cb.message.edit_text(  # type: ignore[union-attr]
        f"❓ Удалить источник «{name}»?\nВиджеты и данные по нему будут удалены.",
        reply_markup=delete_confirm_kb(source_id),
    )


@router.callback_query(F.data.startswith("src_del_ok:"))
async def cb_src_del_ok(cb: CallbackQuery) -> None:
    source_id = int(cb.data.split(":")[1])  # type: ignore[union-attr]
    user_id = cb.from_user.id
    await db.delete_source_for_user(user_id, source_id)
    await cb.answer("Источник удалён")
    text, kb = await _sources_content(user_id)
    await cb.message.edit_text(text, reply_markup=kb)  # type: ignore[union-attr]


@router.callback_query(F.data == "src_back")
async def cb_src_back(cb: CallbackQuery) -> None:
    await cb.answer()
    text, kb = await _sources_content(cb.from_user.id)
    await cb.message.edit_text(text, reply_markup=kb)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Add Source FSM
# ---------------------------------------------------------------------------

@router.message(F.text == "📡 Добавить источник")
async def add_source_start(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await db.upsert_user(msg.from_user.id)  # type: ignore[union-attr]
    await state.set_state(AddSource.name)
    await msg.answer("Введите название источника:", reply_markup=ReplyKeyboardRemove())


@router.message(AddSource.name)
async def fsm_name(msg: Message, state: FSMContext) -> None:
    await state.update_data(name=msg.text)
    await state.set_state(AddSource.company_id)
    await msg.answer("Введите ID компании (число):")


@router.message(AddSource.company_id)
async def fsm_company(msg: Message, state: FSMContext) -> None:
    if not (msg.text or "").strip().isdigit():
        await msg.answer("Нужно ввести число:")
        return
    await state.update_data(company_id=int(msg.text.strip()))  # type: ignore[union-attr]
    await state.set_state(AddSource.api_base_url)
    await msg.answer("Введите API Base URL:\n(например: https://helmetsapi.diwo.tech)")


@router.message(AddSource.api_base_url)
async def fsm_api_url(msg: Message, state: FSMContext) -> None:
    url = (msg.text or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        await msg.answer("Введите корректный URL (должен начинаться с http:// или https://):")
        return
    await state.update_data(api_base_url=url)
    await state.set_state(AddSource.auth_base_url)
    await msg.answer("Введите Auth Base URL:\n(например: https://helmetsauth.diwo.tech)")


@router.message(AddSource.auth_base_url)
async def fsm_auth_url(msg: Message, state: FSMContext) -> None:
    url = (msg.text or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        await msg.answer("Введите корректный URL (должен начинаться с http:// или https://):")
        return
    await state.update_data(auth_base_url=url)
    await state.set_state(AddSource.login)
    await msg.answer("Введите логин:")


@router.message(AddSource.login)
async def fsm_login(msg: Message, state: FSMContext) -> None:
    await state.update_data(login=msg.text)
    await state.set_state(AddSource.password)
    await msg.answer("Введите пароль:")


@router.message(AddSource.password)
async def fsm_password(msg: Message, state: FSMContext) -> None:
    await state.update_data(password=msg.text)
    data = await state.get_data()
    user_id = msg.from_user.id  # type: ignore[union-attr]

    loading = await msg.answer("🔄 Проверяю подключение к API...")
    try:
        total, online = await poller.test_fetch_config(
            auth_base_url=data["auth_base_url"],
            api_base_url=data["api_base_url"],
            login=data["login"],
            password=data["password"],
            company_id=data["company_id"],
        )
    except Exception as exc:
        await state.clear()
        await loading.edit_text(f"⚠️ Проверка не удалась: {_clean_error(exc)}")
        await msg.answer("Источник не добавлен. Проверьте корректность введенных данных.", reply_markup=MAIN_MENU)
        return

    await loading.edit_text(
        f"✅ Подключение успешно!\n\n"
        f"Источник: {data['name']}\n"
        f"Радаров: {total} (online: {online}, offline: {total - online})"
    )

    # Дедупликация: ищем существующий источник с теми же данными
    existing = await db.find_sources_by_credentials(
        company_id=data["company_id"],
        api_base_url=data["api_base_url"],
        auth_base_url=data["auth_base_url"],
        login=data["login"],
    )
    source_id = None
    for candidate in existing:
        if poller.decrypt_password(candidate["password_enc"]) == data["password"]:
            source_id = candidate["id"]
            break

    if source_id is None:
        source_id = await db.add_source(
            name=data["name"],
            company_id=data["company_id"],
            api_base_url=data["api_base_url"],
            auth_base_url=data["auth_base_url"],
            login=data["login"],
            password_enc=poller.encrypt_password(data["password"]),
        )
    await state.clear()
    await db.link_user_source(user_id, source_id)
    await db.upsert_widget(user_id, source_id, wg.WIDGET_SHARP_DROP, is_enabled=True)

    widget_states = {wg.WIDGET_SHARP_DROP: True}
    await msg.answer(
        f"Настройте виджеты для «{data['name']}»:",
        reply_markup=post_add_widgets_kb(source_id, widget_states),
    )


@router.callback_query(F.data.startswith("padwgt:"))
async def cb_padwgt_toggle(cb: CallbackQuery) -> None:
    parts = cb.data.split(":")  # type: ignore[union-attr]
    new_source_id = int(parts[1])
    wtype = parts[2]
    user_id = cb.from_user.id
    w = await db.get_user_widget(user_id, new_source_id, wtype)
    new_enabled = not bool(w and w["is_enabled"])
    await db.upsert_widget(user_id, new_source_id, wtype, is_enabled=new_enabled)

    sharp = await db.get_user_widget(user_id, new_source_id, wg.WIDGET_SHARP_DROP)
    src = await db.get_source(new_source_id)
    name = src["name"] if src else f"#{new_source_id}"
    states = {wg.WIDGET_SHARP_DROP: bool(sharp and sharp["is_enabled"])}
    await cb.answer()
    await cb.message.edit_text(  # type: ignore[union-attr]
        f"Настройте виджеты для «{name}»:",
        reply_markup=post_add_widgets_kb(new_source_id, states),
    )


@router.callback_query(F.data.startswith("padwgt_done:"))
async def cb_padwgt_done(cb: CallbackQuery) -> None:
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]
    await cb.message.answer("✅ Готово! Источник настроен.", reply_markup=MAIN_MENU)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Widget toggle (из "Мои источники")
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("wgt_toggle:"))
async def cb_wgt_toggle(cb: CallbackQuery) -> None:
    parts = cb.data.split(":")  # type: ignore[union-attr]
    source_id = int(parts[1])
    widget_type = parts[2]
    user_id = cb.from_user.id
    w = await db.get_user_widget(user_id, source_id, widget_type)
    new_enabled = not bool(w and w["is_enabled"])
    await db.upsert_widget(user_id, source_id, widget_type, is_enabled=new_enabled)
    await cb.answer("Включён ✅" if new_enabled else "Выключен ❌")
    text, kb = await _source_detail_content(user_id, source_id)
    await cb.message.edit_text(text, reply_markup=kb)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Source: "Сводка сейчас" и "Изменить интервал"
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("src_now:"))
async def cb_src_now(cb: CallbackQuery) -> None:
    source_id = int(cb.data.split(":")[1])  # type: ignore[union-attr]
    await cb.answer("🔄 Запрашиваю...")
    src = await db.get_source(source_id)
    if not src:
        await cb.message.answer("Источник не найден.")  # type: ignore[union-attr]
        return
    try:
        total, online = await poller.test_fetch_source(source_id)
        offline = total - online
        await cb.message.answer(  # type: ignore[union-attr]
            "📊 Сводка по проектам:\n\n" + format_source_summary(src["name"], total, online)
        )
    except Exception as exc:
        await cb.message.answer(f"⚠️ Ошибка: {_clean_error(exc)}")  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@router.message(F.text == "⚙️ Настройки")
async def settings_menu(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await db.upsert_user(msg.from_user.id)  # type: ignore[union-attr]
    text, kb = await _settings_content(msg.from_user.id)  # type: ignore[union-attr]
    await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data == "settings_tz")
async def cb_settings_tz(cb: CallbackQuery) -> None:
    await cb.answer()
    await cb.message.edit_text("Выберите тайм-зону:", reply_markup=tz_inline_kb())  # type: ignore[union-attr]


@router.callback_query(F.data.startswith("tz_set:"))
async def cb_tz_set(cb: CallbackQuery) -> None:
    tz_code = cb.data.split(":", 1)[1]  # type: ignore[union-attr]
    await db.set_user_timezone(cb.from_user.id, tz_code)
    await cb.answer()
    text, kb = await _settings_content(cb.from_user.id)
    await cb.message.edit_text(text, reply_markup=kb)  # type: ignore[union-attr]


@router.callback_query(F.data == "settings_ds")
async def cb_settings_ds(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(SetSummaryTime.input)
    await state.update_data(settings_msg_id=cb.message.message_id, settings_chat_id=cb.message.chat.id)  # type: ignore[union-attr]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Вкл", callback_data="settings_ds_on"),
        InlineKeyboardButton(text="❌ Выкл", callback_data="settings_ds_off"),
        InlineKeyboardButton(text="📊 Сейчас", callback_data="settings_ds_now"),
    ]])
    await cb.message.answer("Введите время сводки (ЧЧ:ММ)", reply_markup=kb)  # type: ignore[union-attr]


@router.callback_query(F.data == "settings_ds_on")
async def cb_settings_ds_on(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await cb.answer()
    user_id = cb.from_user.id
    user = await db.get_user(user_id)
    st = user["summary_time"] if user else "08:00"
    await db.set_user_summary_time(user_id, st)
    await db.upsert_widget(user_id, wg.USER_LEVEL_SOURCE, wg.WIDGET_DAILY_SUMMARY, is_enabled=True)
    data = await state.get_data()
    await state.clear()
    await cb.message.answer(f"✅ Сводка включена: {st}")  # type: ignore[union-attr]
    settings_text, settings_kb = await _settings_content(user_id)
    settings_msg_id = data.get("settings_msg_id")
    if settings_msg_id:
        try:
            await bot.edit_message_text(
                settings_text,
                chat_id=data["settings_chat_id"],
                message_id=settings_msg_id,
                reply_markup=settings_kb,
            )
        except Exception:
            await cb.message.answer(settings_text, reply_markup=settings_kb)  # type: ignore[union-attr]


@router.callback_query(F.data == "settings_ds_off")
async def cb_settings_ds_off(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await cb.answer()
    user_id = cb.from_user.id
    await db.upsert_widget(user_id, wg.USER_LEVEL_SOURCE, wg.WIDGET_DAILY_SUMMARY, is_enabled=False)
    data = await state.get_data()
    await state.clear()
    await cb.message.answer("✅ Сводка отключена.")  # type: ignore[union-attr]
    settings_text, settings_kb = await _settings_content(user_id)
    settings_msg_id = data.get("settings_msg_id")
    if settings_msg_id:
        try:
            await bot.edit_message_text(
                settings_text,
                chat_id=data["settings_chat_id"],
                message_id=settings_msg_id,
                reply_markup=settings_kb,
            )
        except Exception:
            await cb.message.answer(settings_text, reply_markup=settings_kb)  # type: ignore[union-attr]


@router.callback_query(F.data == "settings_ds_now")
async def cb_settings_ds_now(cb: CallbackQuery) -> None:
    await cb.answer("🔄 Запрашиваю...")
    user_id = cb.from_user.id
    sources = await db.get_user_sources(user_id)
    active = [s for s in sources if s["is_active"]]
    if not active:
        await cb.message.answer("Нет активных источников.")  # type: ignore[union-attr]
        return
    blocks = ["📊 Сводка по проектам:"]
    for src in active:
        try:
            total, online = await poller.test_fetch_source(src["id"])
            blocks.append(format_source_summary(src["name"], total, online))
        except Exception as exc:
            blocks.append(f'"{src["name"]}":\n⚠️ {_clean_error(exc)}')
    await cb.message.answer("\n\n".join(blocks))  # type: ignore[union-attr]


@router.message(SetSummaryTime.input)
async def set_summary_time_input(msg: Message, state: FSMContext, bot: Bot) -> None:
    text = (msg.text or "").strip().lower()
    user_id = msg.from_user.id  # type: ignore[union-attr]
    data = await state.get_data()

    if text == "выкл":
        await db.upsert_widget(user_id, wg.USER_LEVEL_SOURCE, wg.WIDGET_DAILY_SUMMARY, is_enabled=False)
        await state.clear()
        await msg.answer("✅ Сводка отключена.")
    else:
        hhmm = _parse_time(text)
        if not hhmm:
            await msg.answer("Неверный формат. Введите ЧЧ:ММ (например: 08:00) или «выкл»:")
            return
        await db.set_user_summary_time(user_id, hhmm)
        await db.upsert_widget(user_id, wg.USER_LEVEL_SOURCE, wg.WIDGET_DAILY_SUMMARY, is_enabled=True)
        await state.clear()
        await msg.answer(f"✅ Время сводки: {hhmm}")

    settings_text, settings_kb = await _settings_content(user_id)
    settings_msg_id = data.get("settings_msg_id")
    if settings_msg_id:
        try:
            await bot.edit_message_text(
                settings_text,
                chat_id=data["settings_chat_id"],
                message_id=settings_msg_id,
                reply_markup=settings_kb,
            )
            return
        except Exception:
            pass
    await msg.answer(settings_text, reply_markup=settings_kb)


# ---------------------------------------------------------------------------
# Snooze (inline, на сообщениях алертов)
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("snooze:"))
async def snooze_alert(cb: CallbackQuery) -> None:
    import time as _time
    parts = cb.data.split(":")  # type: ignore[union-attr]
    source_id, widget_type, days = int(parts[1]), parts[2], int(parts[3])
    await db.set_widget_snoozed(cb.from_user.id, source_id, widget_type, int(_time.time()) + days * 86400)
    await cb.answer("Отключено до завтра" if days == 1 else f"Отключено на {days} дней")
    try:
        await cb.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    return dp
