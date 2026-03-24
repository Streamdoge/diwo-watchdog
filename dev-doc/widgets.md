# Инструкция по добавлению новых виджетов

Виджет — это алгоритм, который анализирует снапшоты и отправляет уведомление пользователям при выполнении условия.

---

## Как устроен виджет

Вся логика виджетов находится в файле `widgets.py`.

Каждый виджет:
1. Имеет уникальный строковый тип (константа)
2. Читает данные из БД (`radar_snapshots`)
3. При выполнении условия вызывает `send_alert(user_id, text)`
4. Управляет состоянием (сработал/не сработал) через словарь в памяти

---

## Шаг за шагом: добавить новый виджет

### 1. Добавить константу типа в `widgets.py`

```python
WIDGET_SHARP_DROP = "sharp_drop"
WIDGET_MY_NEW     = "my_new"   # ← добавить сюда
```

### 2. Написать функцию анализа

```python
async def _check_my_new(
    source_id: int,
    source_name: str,
    total: int,
    online: int,
    send_alert,
) -> None:
    # Ваша логика здесь
    # Пример: алерт если offline > 30%
    if total == 0:
        return
    offline = total - online
    if offline / total > 0.3:
        subscribers = await db.get_widget_subscribers(source_id, WIDGET_MY_NEW)
        text = f"⚠️ Много offline!\nИсточник: {source_name}\nOffline: {offline} из {total}"
        for sub in subscribers:
            await send_alert(sub["user_id"], text)
```

Используйте `db.get_snapshot_around(source_id, target_ts)` чтобы получить снапшот за нужный момент времени.

### 3. Вызвать функцию из `analyze()`

```python
async def analyze(source_id, source_name, total, online, send_alert):
    await _check_sharp_drop(source_id, source_name, total, online, send_alert)
    await _check_my_new(source_id, source_name, total, online, send_alert)  # ← добавить
```

### 4. Автоматически добавлять виджет при добавлении источника (в `bot.py`)

В функции `fsm_interval` найдите:
```python
await db.upsert_widget(user_id, source_id, wg.WIDGET_SHARP_DROP, is_enabled=True)
```
Добавьте рядом:
```python
await db.upsert_widget(user_id, source_id, wg.WIDGET_MY_NEW, is_enabled=True)
```

Виджет появится в разделе «Мои источники» рядом с источником и может быть включён/выключен там же.

---

## Полезные функции БД

```python
# Снапшот примерно N минут назад
prev = await db.get_snapshot_around(source_id, int(time.time()) - 600, window=120)
if prev:
    prev_online = prev["online"]

# Последний снапшот
snap = await db.get_latest_snapshot(source_id)

# Подписчики виджета (учитывает snooze и is_active)
subscribers = await db.get_widget_subscribers(source_id, "my_widget_type")
# → [{"user_id": 12345, "snoozed_until": None}, ...]
```

---

## Паттерн: "алерт один раз, сбрасывать при восстановлении"

```python
_my_alert_active: dict[int, bool] = {}

async def _check_my_new(...):
    is_bad = ...  # ваше условие
    currently_active = _my_alert_active.get(source_id, False)

    if is_bad and not currently_active:
        _my_alert_active[source_id] = True
        # отправить алерт
    elif not is_bad and currently_active:
        _my_alert_active[source_id] = False
        # аномалия устранена
```

---

## Кнопки откладывания (snooze)

Если виджет должен поддерживать кнопки «Отключить до завтра» / «Отключить на неделю» —
просто используйте `wg.WIDGET_MY_NEW` как `widget_type` при генерации клавиатуры.
Функция `alert_snooze_keyboard(source_id, widget_type)` из `bot.py` сгенерирует нужные кнопки.

Обработчик `snooze:` в `bot.py` уже универсальный — работает с любым `widget_type`.

Чтобы алерт отправлялся с кнопками, в `main.py` в функции `on_snapshot` передавайте нужный `widget_type`:

```python
async def send_alert_wrapper(user_id: int, text: str) -> None:
    await alert_sender(user_id, text, source_id, wg.WIDGET_MY_NEW)
```
