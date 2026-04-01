# TASK 03 — AI извлечение имени + подтверждение

## 📋 Задача
Сейчас при вводе имени бот сохраняет ВЕСЬ текст. Если пользователь пишет "Меня зовут Влад" — сохраняется "Меня зовут Влад".
Нужно: AI извлекает только имя + показывает кнопки "Да, верно" / "Изменить".

## 📁 Файл для изменения
`bot.py` — только этот файл.

## ✅ Что нужно сделать

### Шаг 1: Добавить новый state после `STATE_NAME`

Найти блок констант состояний (рядом с `STATE_NAME = 'name'`) и добавить ПОСЛЕ него:

```python
STATE_NAME_CONFIRM = 'name_confirm'
```

### Шаг 2: Добавить AI функцию извлечения имени

Найти функцию `async def ai_parse(...)` и ПЕРЕД ней вставить:

```python
_NAME_EXTRACT_SYS = """Extract ONLY the person's name from the text.
Return ONLY valid JSON, no markdown:
{"name": "Влад"}

Examples:
"Меня зовут Влад" → {"name": "Влад"}
"Я Алишер" → {"name": "Алишер"}
"Влад" → {"name": "Влад"}
"Sardor Toshmatov" → {"name": "Sardor Toshmatov"}
"мое имя влад иванов" → {"name": "Влад Иванов"}

If unclear or no name found, return: {"name": null}"""

async def ai_extract_name(text: str) -> str | None:
    """Извлечь имя из произвольного текста через AI."""
    try:
        raw = await asyncio.to_thread(_chat, _NAME_EXTRACT_SYS, text, 100)
        raw = raw.replace('```json', '').replace('```', '').strip()
        data = json.loads(raw)
        return data.get('name')
    except Exception as e:
        logger.warning(f'Name extraction failed: {e}')
        # Fallback: первое слово с большой буквы
        words = text.strip().split()
        return words[0].capitalize() if words else None
```

### Шаг 3: Добавить STATE_NAME_CONFIRM в `send_onboarding_step()`

Найти блок `if state == STATE_NAME:` в функции `send_onboarding_step()` и ПОСЛЕ него добавить:

```python
    elif state == STATE_NAME_CONFIRM:
        name = u.get('name', '')
        if lang == 'ru':
            confirm_msg = f"Тебя зовут *{name}*?"
            yes_btn = '✅ Да, всё верно'
            edit_btn = '✏️ Изменить'
        else:
            confirm_msg = f"Ismingiz *{name}*mi?"
            yes_btn = "✅ Ha, to'g'ri"
            edit_btn = "✏️ O'zgartirish"
        kb = [[
            InlineKeyboardButton(yes_btn,  callback_data='name_ok'),
            InlineKeyboardButton(edit_btn, callback_data='name_edit'),
        ]]
        await context.bot.send_message(
            chat_id, confirm_msg,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )
```

### Шаг 4: Обновить обработку STATE_NAME в `on_text()`

Найти в функции `on_text()`:
```python
        if state == STATE_NAME:
            set_user(uid, name=text, onboarding_state=STATE_INCOME_FREQ)
            await send_onboarding_step(chat_id, uid, STATE_INCOME_FREQ, ctx)
            return
```

Заменить на:
```python
        if state == STATE_NAME:
            extracted = await ai_extract_name(text)
            if extracted:
                set_user(uid, name=extracted, onboarding_state=STATE_NAME_CONFIRM)
                await send_onboarding_step(chat_id, uid, STATE_NAME_CONFIRM, ctx)
            else:
                if lang == 'ru':
                    await upd.message.reply_text('🤔 Не расслышал имя. Напиши ещё раз — только имя!')
                else:
                    await upd.message.reply_text("🤔 Ismni tushunmadim. Faqat ismingizni yozing!")
            return
```

### Шаг 5: Обновить обработку STATE_NAME в `on_voice()`

Найти в функции `on_voice()`:
```python
        if state == STATE_NAME:
            set_user(uid, name=transcript, onboarding_state=STATE_INCOME_FREQ)
            await msg.edit_text(f'🎤 _{transcript}_\n\n✅ Отлично!', parse_mode='Markdown')
            await send_onboarding_step(chat_id, uid, STATE_INCOME_FREQ, ctx)
            return
```

Заменить на:
```python
        if state == STATE_NAME:
            extracted = await ai_extract_name(transcript)
            if extracted:
                set_user(uid, name=extracted, onboarding_state=STATE_NAME_CONFIRM)
                await msg.edit_text(f'🎤 _{transcript}_\n\n✅ Понял!', parse_mode='Markdown')
                await send_onboarding_step(chat_id, uid, STATE_NAME_CONFIRM, ctx)
            else:
                await msg.edit_text(
                    f'🎤 _{transcript}_\n\n🤔 Не расслышал имя. Повтори погромче!'
                    if lang == 'ru' else
                    f"🎤 _{transcript}_\n\n🤔 Ismni tushunmadim. Qaytadan ayting!",
                    parse_mode='Markdown'
                )
            return
```

### Шаг 6: Добавить обработку кнопок `name_ok` и `name_edit` в `on_callback()`

Найти в функции `on_callback()` блок `# ─── LANGUAGE SELECTION ───` и ПОСЛЕ него (но до блока `# ─── ONBOARDING CALLBACKS ───`) добавить:

```python
    # ─── NAME CONFIRMATION ───
    if data == 'name_ok':
        set_user(uid, onboarding_state=STATE_INCOME_FREQ)
        await q.edit_message_text('✅')
        await send_onboarding_step(chat_id, uid, STATE_INCOME_FREQ, ctx)
        return

    if data == 'name_edit':
        set_user(uid, onboarding_state=STATE_NAME)
        if lang == 'ru':
            await q.edit_message_text('✏️ Хорошо, напиши своё имя ещё раз:')
        else:
            await q.edit_message_text("✏️ Yaxshi, ismingizni qayta yozing:")
        return
```

## 🧪 Как проверить
1. `/start` → выбрать язык → написать "Меня зовут Влад" → должно спросить "Тебя зовут *Влад*?"
2. Нажать "Да, всё верно" → переход к вопросу о частоте дохода
3. Нажать "Изменить" → вернуться к вводу имени
4. Написать просто "Влад" → тоже работает
5. Написать что-то непонятное → просит повторить
6. Голосом сказать имя → AI извлекает правильно

## ⚠️ Важно
- `STATE_NAME_CONFIRM` должен быть добавлен до существующих states
- `ai_extract_name()` — СИНХРОННЫЙ вызов _chat через asyncio.to_thread
- JSON от AI может не распарситься — fallback берёт первое слово
