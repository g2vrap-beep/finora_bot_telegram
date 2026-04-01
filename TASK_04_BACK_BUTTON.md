# TASK 04 — Кнопка "Назад" в онбординге

## 📋 Задача
Добавить кнопку "← Назад" на все шаги онбординга. Сейчас если пользователь ошибся — нужно заново /start.
После этой задачи можно вернуться на любой предыдущий шаг.

## 📁 Файл для изменения
`bot.py` — только этот файл.

## ⚠️ ВАЖНО: выполнять ПОСЛЕ TASK_03 (там добавляется STATE_NAME_CONFIRM)

## ✅ Что нужно сделать

### Шаг 1: Добавить карту навигации "Назад"

Найти блок с константами states (после STATE_DONE) и добавить:

```python
# Карта навигации: текущий state → предыдущий state
ONBOARDING_BACK_MAP = {
    'name_confirm':  STATE_NAME,
    STATE_INCOME_FREQ: 'name_confirm',  # после TASK_03; если TASK_03 не сделан — STATE_NAME
    STATE_INCOME_AMT:  STATE_INCOME_FREQ,
    STATE_CURRENCY:    STATE_INCOME_AMT,
    STATE_SIDE_HUSTLE: STATE_CURRENCY,
    STATE_SIDE_AMT:    STATE_SIDE_HUSTLE,
    STATE_GOAL_CUSTOM: STATE_GOAL,
    STATE_NOTIFY_TIME: STATE_NOTIFY_WHY,
}

def get_prev_state(uid: int, current_state: str, user_data: dict) -> str | None:
    """Получить предыдущий state с учётом динамической логики."""
    u = get_user(uid)

    if current_state == STATE_GOAL:
        return STATE_SIDE_AMT if u.get('side_income', 0) > 0 else STATE_SIDE_HUSTLE

    if current_state == STATE_NOTIFY_WHY:
        return STATE_GOAL_CUSTOM if u.get('goal', '') else STATE_GOAL

    return ONBOARDING_BACK_MAP.get(current_state)
```

### Шаг 2: Полностью заменить функцию `send_onboarding_step()`

Целиком заменить существующую функцию на новую версию с кнопкой "Назад":

```python
async def send_onboarding_step(chat_id: int, uid: int, state: str, context):
    u    = get_user(uid)
    lang = u.get('language', 'ru')
    name = u.get('name', '')

    back_text = '← Назад' if lang == 'ru' else '← Orqaga'
    back_btn  = InlineKeyboardButton(back_text, callback_data='onb_back')

    if state == STATE_NAME:
        await context.bot.send_message(chat_id, tx(lang, 'ask_name'), parse_mode='Markdown')

    elif state == 'name_confirm':
        if lang == 'ru':
            confirm_msg = f"Тебя зовут *{name}*?"
            yes_btn  = '✅ Да, всё верно'
            edit_btn = '✏️ Изменить'
        else:
            confirm_msg = f"Ismingiz *{name}*mi?"
            yes_btn  = "✅ Ha, to'g'ri"
            edit_btn = "✏️ O'zgartirish"
        kb = [[
            InlineKeyboardButton(yes_btn,  callback_data='name_ok'),
            InlineKeyboardButton(edit_btn, callback_data='name_edit'),
        ], [back_btn]]
        await context.bot.send_message(
            chat_id, confirm_msg,
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_INCOME_FREQ:
        kb = [[
            InlineKeyboardButton(tx(lang, 'freq_daily'),    callback_data='freq_daily'),
            InlineKeyboardButton(tx(lang, 'freq_weekly'),   callback_data='freq_weekly'),
        ], [
            InlineKeyboardButton(tx(lang, 'freq_monthly'),  callback_data='freq_monthly'),
            InlineKeyboardButton(tx(lang, 'freq_irregular'),callback_data='freq_irregular'),
        ], [back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_income_freq', name=name),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_INCOME_AMT:
        kb = [[back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_income_amt'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_CURRENCY:
        kb = [[
            InlineKeyboardButton(tx(lang, 'cur_uzs'), callback_data='cur_UZS'),
            InlineKeyboardButton(tx(lang, 'cur_usd'), callback_data='cur_USD'),
            InlineKeyboardButton(tx(lang, 'cur_rub'), callback_data='cur_RUB'),
        ], [back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_currency'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_SIDE_HUSTLE:
        kb = [[
            InlineKeyboardButton(tx(lang, 'yes'), callback_data='side_yes'),
            InlineKeyboardButton(tx(lang, 'no'),  callback_data='side_no'),
        ], [back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_side_hustle'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_SIDE_AMT:
        kb = [[back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_side_amt'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_GOAL:
        kb = [
            [InlineKeyboardButton(tx(lang, 'goal_save'),     callback_data='goal_save'),
             InlineKeyboardButton(tx(lang, 'goal_buy'),      callback_data='goal_buy')],
            [InlineKeyboardButton(tx(lang, 'goal_invest'),   callback_data='goal_invest'),
             InlineKeyboardButton(tx(lang, 'goal_debt'),     callback_data='goal_debt')],
            [InlineKeyboardButton(tx(lang, 'goal_business'), callback_data='goal_business')],
            [InlineKeyboardButton(tx(lang, 'goal_none'),     callback_data='goal_none')],
            [back_btn]
        ]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_goal', name=name),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_GOAL_CUSTOM:
        kb = [[back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_goal_custom'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_NOTIFY_WHY:
        kb = [
            [InlineKeyboardButton(tx(lang, 'notify_18'), callback_data='notify_18:00'),
             InlineKeyboardButton(tx(lang, 'notify_19'), callback_data='notify_19:00')],
            [InlineKeyboardButton(tx(lang, 'notify_20'), callback_data='notify_20:00'),
             InlineKeyboardButton(tx(lang, 'notify_21'), callback_data='notify_21:00')],
            [InlineKeyboardButton(tx(lang, 'notify_22'), callback_data='notify_22:00'),
             InlineKeyboardButton(tx(lang, 'notify_23'), callback_data='notify_23:00')],
            [InlineKeyboardButton(tx(lang, 'notify_custom'), callback_data='notify_custom')],
            [back_btn]
        ]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_notify_why', name=name),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_NOTIFY_TIME:
        kb = [[back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_notify_time'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )
```

### Шаг 3: Добавить обработку кнопки `onb_back` в `on_callback()`

В функции `on_callback()` найти блок `# ─── LANGUAGE SELECTION ───` и после блока NAME CONFIRMATION (добавленного в TASK_03) добавить:

```python
    # ─── BACK BUTTON (ONBOARDING) ───
    if data == 'onb_back':
        prev = get_prev_state(uid, state, ctx.user_data)
        if not prev:
            await q.answer(
                '⚠️ Это первый шаг!' if lang == 'ru' else '⚠️ Bu birinchi qadam!'
            )
            return
        set_user(uid, onboarding_state=prev)
        try:
            await q.message.delete()
        except Exception:
            pass
        await send_onboarding_step(chat_id, uid, prev, ctx)
        return
```

## 🧪 Как проверить
1. Пройти несколько шагов онбординга
2. На кнопках должна появиться кнопка "← Назад"
3. Нажать "← Назад" → должен вернуться на предыдущий шаг
4. На первом шаге (STATE_INCOME_FREQ / STATE_NAME) "← Назад" должен показать уведомление "Это первый шаг!"
5. Шаги без кнопок (STATE_INCOME_AMT, STATE_SIDE_AMT, etc.) — показывают inline кнопку "← Назад"

## ⚠️ Важно
- Если TASK_03 НЕ выполнен: в ONBOARDING_BACK_MAP заменить `'name_confirm'` на `STATE_NAME`
- STATE_INCOME_FREQ → 'name_confirm' (если TASK_03 выполнен) или STATE_NAME (если не выполнен)
- Функция `send_onboarding_step()` заменяется ЦЕЛИКОМ (она небольшая)
- `get_prev_state()` принимает `user_data: dict` но ctx.user_data можно передавать как есть
