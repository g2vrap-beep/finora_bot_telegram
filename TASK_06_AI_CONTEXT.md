# TASK 06 — AI-друг с историей диалога

## 📋 Задача
Сейчас AI не помнит предыдущие сообщения — каждый диалог начинается с нуля.
Нужно: хранить последние 10 сообщений пользователя в context.user_data и передавать их в AI.
Также улучшить system prompt — сделать AI более дружеским и тёплым.

## 📁 Файл для изменения
`bot.py` — только этот файл.

## ✅ Что нужно сделать

### Шаг 1: Заменить функцию `build_advisor_system()`

Найти существующую функцию `def build_advisor_system(...)` и заменить ЦЕЛИКОМ:

```python
def build_advisor_system(user: dict, lang: str) -> str:
    name   = user.get('name', '')
    goal   = user.get('goal', '')
    income = user.get('income_amt', 0)
    cur    = user.get('income_currency', 'UZS')
    side   = user.get('side_income', 0)
    freq   = user.get('income_freq', '')

    if lang == 'uz':
        goal_uz = goal if goal else "yo'q"
        return (
            f"Siz Finora — {name} ning shaxsiy moliyaviy do'stisiz. "
            f"Birgalikda moliyaviy erkinlikka intilasiz.\n\n"
            f"MA'LUMOT:\n"
            f"• Daromad: {income} {cur} ({freq})" +
            (f"\n• Qo'shimcha: {side} {cur}" if side else '') +
            f"\n• Maqsad: {goal_uz}\n\n"
            f"SIZNING ROLINGIZ:\n"
            f"Siz faqat sovetchidan emas, balki samimiy do'stsiz. "
            f"O'zbek tilida gaplashing. Qisqa, amaliy, do'stona, emotsional. "
            f"Emoji ishlating 💬. Qo'llab-quvvatlang, tushunib bering, ilhomlantiring.\n\n"
            f"MASLAHATLAR:\n"
            f"O'zbekiston uchun real moliyaviy vositalar: "
            f"depozitlar (Kapitalbank, Hamkorbank), UZSE aksiyalari, oltin, ko'chmas mulk."
        )
    else:
        return (
            f"Ты — Finora, личный финансовый друг {name}. "
            f"Вы вместе идёте к финансовой свободе.\n\n"
            f"ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ:\n"
            f"• Доход: {income} {cur} ({freq})" +
            (f"\n• Доп. доход: {side} {cur}" if side else '') +
            f"\n• Финансовая цель: {goal or 'не задана'}\n\n"
            f"ТВОЯ РОЛЬ:\n"
            f"Ты не просто советник, а настоящий друг. Говори по-русски. "
            f"Коротко, практично, по-дружески, с теплом и эмоциями. "
            f"Используй эмодзи 💬. Поддерживай, понимай, вдохновляй. "
            f"Помни контекст разговора — ты уже знаешь этого человека.\n\n"
            f"СОВЕТУЙ РЕАЛЬНЫЕ ИНСТРУМЕНТЫ ДЛЯ УЗБЕКИСТАНА:\n"
            f"Депозиты (Kapitalbank, Hamkorbank 18-22%), акции UZSE, золото, недвижимость."
        )
```

### Шаг 2: Заменить функцию `ai_chat()`

Найти существующую `async def ai_chat(...)` и заменить ЦЕЛИКОМ:

```python
async def ai_chat(uid: int, lang: str, text: str, context: ContextTypes.DEFAULT_TYPE = None) -> str:
    """Дружеский разговор с AI с сохранением истории диалога."""
    user  = get_user(uid)
    stats = get_stats(uid)

    fin_ctx = (
        f"Финансы: доходы {uzs(stats['inc'])}, расходы {uzs(stats['exp'])}, "
        f"баланс {uzs(stats['inc'] - stats['exp'])}. "
        f"Этот месяц: доходы {uzs(stats['m_inc'])}, расходы {uzs(stats['m_exp'])}."
    )
    sys_prompt = build_advisor_system(user, lang) + f"\n\nТекущая финансовая ситуация: {fin_ctx}"

    # История диалога из context.user_data
    chat_history = []
    if context is not None:
        chat_history = context.user_data.get('chat_history', [])

    # Построить messages
    messages = [{'role': 'system', 'content': sys_prompt}]
    for h in chat_history[-10:]:   # последние 10 сообщений
        messages.append(h)
    messages.append({'role': 'user', 'content': text})

    try:
        response = client.chat.completions.create(
            model=OR_MODEL,
            max_tokens=600,
            messages=messages
        )
        reply = response.choices[0].message.content.strip().replace('**', '*')

        # Сохранить в историю
        if context is not None:
            chat_history.append({'role': 'user',      'content': text})
            chat_history.append({'role': 'assistant', 'content': reply})
            # Ограничить до 20 сообщений (10 пар)
            if len(chat_history) > 20:
                chat_history = chat_history[-20:]
            context.user_data['chat_history'] = chat_history

        return reply
    except Exception as e:
        logger.error(f'AI chat error: {e}')
        return '❌ Извини, что-то пошло не так. Попробуй ещё раз!' if lang == 'ru' else '❌ Kechirasiz, xatolik. Qayta urinib ko\'ring!'
```

### Шаг 3: Обновить вызовы `ai_chat()` в handlers — передавать `ctx`

В функции `on_text()` найти вызов:
```python
        reply = await ai_chat(uid, lang, text)
```
Заменить на:
```python
        reply = await ai_chat(uid, lang, text, ctx)
```

В функции `on_voice()` найти вызов:
```python
        reply = await ai_chat(uid, lang, transcript)
```
Заменить на:
```python
        reply = await ai_chat(uid, lang, transcript, ctx)
```

### Шаг 4: Добавить очистку истории при /start

В функции `cmd_start()` найти строку `set_user(uid, ...)` и после неё добавить:
```python
    ctx.user_data.pop('chat_history', None)  # Сбросить историю диалога
```

## 🧪 Как проверить
1. Написать боту: "Привет, у меня вопрос по финансам"
2. AI отвечает
3. Написать: "А если я хочу начать инвестировать?"
4. AI должен помнить контекст первого вопроса и ответить связно
5. Снова написать: "Расскажи подробнее про первый вариант"
6. AI должен помнить, что говорил об инвестициях

## ⚠️ Важно
- `context.user_data` — это словарь в памяти, сбрасывается при перезапуске бота
- Это нормально — история диалога временная, не для долгосрочного хранения
- `ai_chat()` теперь принимает `context=None` — обратная совместимость сохранена
- Не использовать `asyncio.to_thread` для нового `ai_chat()` — он уже вызывает `client.chat.completions.create` напрямую (синхронно внутри async функции). Если нужно — обернуть в `asyncio.to_thread` при необходимости
