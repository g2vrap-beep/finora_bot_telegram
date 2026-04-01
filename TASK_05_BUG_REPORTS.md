# TASK 05 — Система баг-репортов

## 📋 Задача
Добавить команду `/bug` — пользователь описывает проблему, бот отправляет репорт администратору.
Администратор получает уведомление с кнопками "Ответить" / "Решено".
Добавить команду `/bugs` для просмотра открытых багов (только для админа).

## 📁 Файл для изменения
`bot.py` — только этот файл.

## ✅ Что нужно сделать

### Шаг 1: Добавить новый state

В блок констант states добавить:
```python
STATE_BUG_REPORT = 'bug_report'
```

### Шаг 2: Добавить таблицу `bug_reports` в `init_db()`

В функции `init_db()` после создания таблицы `users` добавить:
```python
            c.execute('''CREATE TABLE IF NOT EXISTS bug_reports(
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                username TEXT,
                description TEXT,
                status TEXT DEFAULT 'open',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                resolved_at TIMESTAMPTZ
            )''')
```

### Шаг 3: Добавить функцию `handle_bug_report()`

Вставить перед блоком `# ────────────────────────── HANDLERS ──────────────────────────────`:

```python
async def handle_bug_report(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str, is_voice: bool = False):
    """Обработать и сохранить баг-репорт, уведомить админа."""
    uid      = upd.effective_user.id
    user     = get_user(uid)
    lang     = user.get('language', 'ru')
    name     = user.get('name', 'Пользователь')
    username = upd.effective_user.username or 'no_username'

    # Сохранить в БД
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(
                'INSERT INTO bug_reports(user_id, username, description) VALUES(%s,%s,%s) RETURNING id',
                (uid, username, text)
            )
            report_id = c.fetchone()[0]
        conn.commit()

    # Контекст пользователя
    stats = get_stats(uid)
    state = user.get('onboarding_state', 'unknown')

    # Отправить Admin
    admin_id  = int(os.getenv('ADMIN_USER_ID', '1326256223'))
    admin_msg = (
        f"🐛 *НОВЫЙ БАГ-РЕПОРТ #{report_id}*\n\n"
        f"👤 {name} (@{username})\n"
        f"🆔 User ID: `{uid}`\n"
        f"📅 {datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}\n"
        f"🎤 Голосовое: {'Да' if is_voice else 'Нет'}\n\n"
        f"📝 *ОПИСАНИЕ:*\n{text}\n\n"
        f"📊 *КОНТЕКСТ:*\n"
        f"• Язык: {lang}\n"
        f"• State: {state}\n"
        f"• Транзакций: {stats['count']}\n"
        f"• Баланс: {uzs(stats['inc'] - stats['exp'])}"
    )
    kb_admin = [[
        InlineKeyboardButton('✅ Решено', callback_data=f'resolve_{report_id}'),
    ]]
    try:
        await ctx.bot.send_message(
            admin_id, admin_msg,
            reply_markup=InlineKeyboardMarkup(kb_admin),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f'Failed to send bug report to admin: {e}')

    # Сбросить state пользователя
    set_user(uid, onboarding_state=STATE_DONE)

    # Подтверждение пользователю
    if lang == 'ru':
        confirm = (
            f"✅ *Спасибо за репорт!*\n\n"
            f"Твоё сообщение отправлено разработчику.\n"
            f"Мы исправим это как можно быстрее! 🚀\n\n"
            f"ID репорта: *#{report_id}*\n\n"
            f"💬 Обычно отвечаем в течение 24 часов."
        )
    else:
        confirm = (
            f"✅ *Hisobot uchun rahmat!*\n\n"
            f"Xabaringiz dasturchiga yuborildi.\n"
            f"Buni tezda tuzatamiz! 🚀\n\n"
            f"Hisobot ID: *#{report_id}*\n\n"
            f"💬 Odatda 24 soat ichida javob beramiz."
        )
    await upd.message.reply_text(confirm, parse_mode='Markdown')
```

### Шаг 4: Добавить команды `/bug` и `/bugs`

Вставить после функции `handle_bug_report()`:

```python
async def cmd_bug(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    set_user(uid, onboarding_state=STATE_BUG_REPORT)
    if lang == 'ru':
        msg = (
            '🐛 *Нашёл ошибку или баг?*\n\n'
            'Опиши проблему подробно:\n'
            '• Что делал?\n'
            '• Что пошло не так?\n'
            '• Когда произошло?\n\n'
            'Можешь написать текстом или голосовым 🎤'
        )
    else:
        msg = (
            '🐛 *Xatolik yoki bug topdingizmi?*\n\n'
            'Muammoni batafsil tasvirlab bering:\n'
            '• Nima qildingiz?\n'
            '• Nima noto\'g\'ri ketdi?\n'
            '• Qachon sodir bo\'ldi?\n\n'
            'Matn yoki ovozli xabar yuborishingiz mumkin 🎤'
        )
    kb = [[InlineKeyboardButton(
        '← Отмена' if lang == 'ru' else '← Bekor qilish',
        callback_data='cancel_bug'
    )]]
    await upd.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def cmd_bugs(upd: Update, _):
    """Просмотр открытых багов — только для админа."""
    uid      = upd.effective_user.id
    admin_id = int(os.getenv('ADMIN_USER_ID', '1326256223'))
    if uid != admin_id:
        return
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT id, username, description, created_at FROM bug_reports WHERE status='open' ORDER BY created_at DESC LIMIT 20"
            )
            bugs = c.fetchall()
    if not bugs:
        await upd.message.reply_text('✅ Нет открытых багов!'); return
    msg = f"🐛 *ОТКРЫТЫЕ БАГИ ({len(bugs)}):*\n\n"
    for id_, username, desc, dt in bugs:
        short = desc[:80] + '...' if len(desc) > 80 else desc
        msg  += f"*#{id_}* @{username}\n_{short}_\n{dt.strftime('%d.%m %H:%M')}\n\n"
    await upd.message.reply_text(msg, parse_mode='Markdown')
```

### Шаг 5: Добавить обработку в `on_text()` и `on_voice()`

В `on_text()` — в блоке `# ─── MAIN BOT LOGIC ───` (когда done=1), ПЕРЕД строкой `await ctx.bot.send_chat_action(...)` добавить проверку:

```python
    # Баг-репорт mode
    if state == STATE_BUG_REPORT:
        await handle_bug_report(upd, ctx, text, is_voice=False)
        return
```

В `on_voice()` — после блока обработки онбординга (`if not done:` ... `return`), в начале `# ─── MAIN BOT LOGIC ───` добавить:

```python
    # Баг-репорт mode
    if state == STATE_BUG_REPORT:
        await handle_bug_report(upd, ctx, transcript, is_voice=True)
        return
```

### Шаг 6: Добавить обработку кнопок в `on_callback()`

В блоке `# ─── MAIN CALLBACKS ───` в функции `on_callback()` добавить:

```python
    if data == 'cancel_bug':
        set_user(uid, onboarding_state=STATE_DONE)
        text = '❌ Отменено.' if lang == 'ru' else '❌ Bekor qilindi.'
        await q.edit_message_text(text)
        return

    if data.startswith('resolve_'):
        admin_id = int(os.getenv('ADMIN_USER_ID', '1326256223'))
        if uid != admin_id:
            await q.answer('❌ Нет доступа')
            return
        report_id = int(data[8:])
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute(
                    "UPDATE bug_reports SET status='resolved', resolved_at=NOW() WHERE id=%s",
                    (report_id,)
                )
            conn.commit()
        await q.answer('✅ Отмечено решённым!')
        current_text = q.message.text or ''
        await q.edit_message_text(current_text + '\n\n✅ *РЕШЕНО*', parse_mode='Markdown')
        return
```

### Шаг 7: Зарегистрировать команды в `main()`

Найти блок добавления handlers и добавить:
```python
    app.add_handler(CommandHandler('bug',  cmd_bug))
    app.add_handler(CommandHandler('bugs', cmd_bugs))
```

### Шаг 8: Добавить `/bug` в список команд бота

В функции `setup_bot_ui()` в список `commands` добавить:
```python
        BotCommand('bug', '🐛 Сообщить об ошибке'),
```

## 🧪 Как проверить
1. Написать `/bug` → бот просит описать проблему
2. Описать проблему текстом → приходит подтверждение с номером репорта
3. Администратору (ID 1326256223) приходит уведомление с кнопкой "Решено"
4. Нажать "Решено" → сообщение помечается как решённое
5. `/bugs` от имени НЕ-администратора → бот не отвечает
6. `/bugs` от имени администратора → список открытых багов

## ⚠️ Важно
- `ADMIN_USER_ID` берётся из переменной окружения (`os.getenv('ADMIN_USER_ID', '1326256223')`)
- STATE_BUG_REPORT должен быть добавлен как константа
- После сохранения репорта — state сбрасывается в STATE_DONE
- Проверить что `STATE_DONE` это `'done'` — да, это так
