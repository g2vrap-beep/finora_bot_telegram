# TASK 08 — Полная система долгов и кредитов

## 📋 Задача
Добавить полноценную систему учёта кредитов:
- При выборе цели "Закрыть долги/кредиты" в онбординге — собирать данные по каждому кредиту
- Команда `/debts` — просмотр и управление кредитами
- AI генерирует стратегию погашения методом лавины (самый высокий %)
- Детекция нового кредита в обычной переписке

## 📁 Файл для изменения
`bot.py` — только этот файл.

## ✅ Что нужно сделать (8 шагов)

---

### Шаг 1: Добавить 6 новых states для долгов

В блок констант states (после STATE_DONE или STATE_BUG_REPORT) добавить:

```python
STATE_DEBT_COUNT    = 'debt_count'    # Сколько кредитов?
STATE_DEBT_BANK     = 'debt_bank'     # Название банка
STATE_DEBT_AMT      = 'debt_amt'      # Сумма долга
STATE_DEBT_RATE     = 'debt_rate'     # Процентная ставка
STATE_DEBT_MONTHLY  = 'debt_monthly'  # Ежемесячный платёж
STATE_DEBT_DEADLINE = 'debt_deadline' # Срок погашения
```

---

### Шаг 2: Добавить таблицу `debts` в `init_db()`

В функции `init_db()` после создания таблицы `bug_reports` (или `users`) добавить:

```python
            c.execute('''CREATE TABLE IF NOT EXISTS debts(
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                bank TEXT,
                amount FLOAT,
                rate FLOAT,
                monthly_payment FLOAT,
                deadline TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )''')
```

---

### Шаг 3: Добавить AI-функцию генерации стратегии погашения

Вставить ПЕРЕД блоком `# ────────────────────────── HANDLERS ──────────────────────────────`:

```python
async def generate_debt_strategy(uid: int, lang: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Генерирует AI-стратегию погашения долгов и переходит к следующему шагу онбординга."""
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(
                'SELECT bank, amount, rate, monthly_payment, deadline FROM debts WHERE user_id=%s ORDER BY rate DESC',
                (uid,)
            )
            debts = c.fetchall()

    if not debts:
        set_user(uid, onboarding_state=STATE_NOTIFY_WHY)
        await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, context)
        return

    debt_list = '\n'.join(
        f"  {i+1}. {bank}: {uzs(amount)} (ставка {rate}%/год, платёж {uzs(monthly)}/мес, срок: {deadline})"
        for i, (bank, amount, rate, monthly, deadline) in enumerate(debts)
    )
    total_debt    = sum(d[1] for d in debts)
    total_monthly = sum(d[3] for d in debts)
    user   = get_user(uid)
    income = user.get('income_amt', 0)

    prompt = (
        f"Пользователь в кредитной кабале. Помоги составить план выхода.\n\n"
        f"КРЕДИТЫ ({len(debts)} шт):\n{debt_list}\n\n"
        f"ИТОГО долгов: {uzs(total_debt)}\n"
        f"Платежей в месяц: {uzs(total_monthly)}\n"
        f"Доход: {uzs(income)}\n\n"
        f"ЗАДАЧА:\n"
        f"1. Определи какой кредит закрывать ПЕРВЫМ (метод лавины — самый высокий %)\n"
        f"2. Дай конкретный план на 3-6 месяцев\n"
        f"3. Мотивируй — покажи что выход есть!\n"
        f"4. Если доход меньше платежей — дай совет\n"
        f"5. Коротко, по-дружески, с эмодзи, на {'русском' if lang == 'ru' else 'узбекском'}"
    )
    sys_prompt = (
        f"Ты Finora — финансовый советник. Помоги выбраться из долгов. "
        f"Говори {'по-русски' if lang == 'ru' else 'на узбекском'}. "
        f"Коротко, тепло, практично, с надеждой. Используй эмодзи."
    )

    try:
        strategy = await asyncio.to_thread(_chat, sys_prompt, prompt, 800)
        strategy = strategy.replace('**', '*')
        footer = (
            "_Стратегию можно просмотреть командой /debts_"
            if lang == 'ru' else
            "_Strategiyani /debts buyrug'i bilan ko'rish mumkin_"
        )
        await context.bot.send_message(
            chat_id,
            f"💎 *Твоя персональная стратегия погашения долгов:*\n\n{strategy}\n\n{footer}"
            if lang == 'ru' else
            f"💎 *Sizning kreditlarni to'lash strategiyangiz:*\n\n{strategy}\n\n{footer}",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f'Debt strategy generation failed: {e}')
        msg = '✅ Кредиты записаны! Стратегию смотри в /debts' if lang == 'ru' else '✅ Kreditlar yozildi! Strategiyani /debts da ko\'ring'
        await context.bot.send_message(chat_id, msg)

    set_user(uid, onboarding_state=STATE_NOTIFY_WHY)
    await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, context)
```

---

### Шаг 4: Добавить шаги долгов в `send_onboarding_step()`

В функции `send_onboarding_step()` добавить новые elif-блоки ПОСЛЕ блока `elif state == STATE_NOTIFY_TIME:`:

```python
    elif state == STATE_DEBT_COUNT:
        if lang == 'ru':
            msg = ('💳 *Сколько у тебя кредитов?*\n\n'
                   'Напиши число (например: *3*)\n\n'
                   '💡 Я помогу составить план их погашения!')
        else:
            msg = ('💳 *Nechta kreditingiz bor?*\n\n'
                   'Raqamni yozing (masalan: *3*)\n\n'
                   "💡 Men ularni to'lash rejasini tuzishga yordam beraman!")
        back_text = '← Назад' if lang == 'ru' else '← Orqaga'
        kb = [[InlineKeyboardButton(back_text, callback_data='onb_back')]]
        await context.bot.send_message(chat_id, msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    elif state == STATE_DEBT_BANK:
        current = context.user_data.get('debt_current', 0) + 1
        total   = context.user_data.get('debt_target', 1)
        if lang == 'ru':
            msg = (f'🏦 *Кредит {current} из {total}*\n\n'
                   f'Название банка или откуда кредит?\n'
                   f'(например: *Kapitalbank* или *Микрофинанс*)')
        else:
            msg = (f'🏦 *Kredit {current} / {total}*\n\n'
                   f'Bank nomi yoki kredit qayerdan?\n'
                   f'(masalan: *Kapitalbank* yoki *Mikrofinans*)')
        back_text = '← Назад' if lang == 'ru' else '← Orqaga'
        kb = [[InlineKeyboardButton(back_text, callback_data='onb_back')]]
        await context.bot.send_message(chat_id, msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    elif state == STATE_DEBT_AMT:
        msg = ('💰 *Сколько осталось платить?*\n\nНапиши остаток долга (например: *5000000*)' if lang == 'ru'
               else "💰 *Qancha qarz qoldi?*\n\nQolgan qarzni yozing (masalan: *5000000*)")
        back_text = '← Назад' if lang == 'ru' else '← Orqaga'
        kb = [[InlineKeyboardButton(back_text, callback_data='onb_back')]]
        await context.bot.send_message(chat_id, msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    elif state == STATE_DEBT_RATE:
        msg = ('📊 *Какая процентная ставка?*\n\nНапиши проценты годовых (например: *24* или *24%*)' if lang == 'ru'
               else "📊 *Foiz stavkasi qancha?*\n\nYillik foizni yozing (masalan: *24* yoki *24%*)")
        back_text = '← Назад' if lang == 'ru' else '← Orqaga'
        kb = [[InlineKeyboardButton(back_text, callback_data='onb_back')]]
        await context.bot.send_message(chat_id, msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    elif state == STATE_DEBT_MONTHLY:
        msg = ("📅 *Сколько платишь в месяц?*\n\nНапиши ежемесячный платёж (например: *250000*)" if lang == 'ru'
               else "📅 *Oyiga qancha to'laysiz?*\n\nOylik to'lovni yozing (masalan: *250000*)")
        back_text = '← Назад' if lang == 'ru' else '← Orqaga'
        kb = [[InlineKeyboardButton(back_text, callback_data='onb_back')]]
        await context.bot.send_message(chat_id, msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    elif state == STATE_DEBT_DEADLINE:
        msg = ("⏰ *До какого срока нужно закрыть?*\n\nНапиши дату или период (например: *Декабрь 2026* или *12 месяцев*)" if lang == 'ru'
               else "⏰ *Qachongacha to'lash kerak?*\n\nSana yoki muddatni yozing (masalan: *Dekabr 2026* yoki *12 oy*)")
        back_text = '← Назад' if lang == 'ru' else '← Orqaga'
        kb = [[InlineKeyboardButton(back_text, callback_data='onb_back')]]
        await context.bot.send_message(chat_id, msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
```

---

### Шаг 5: Обработать выбор цели "goal_debt" в `on_callback()`

В функции `on_callback()`, в блоке `if data.startswith('goal_'):`, найти строку:
```python
            goal_val = goal_map[data][0 if lang == 'ru' else 1]
            set_user(uid, goal=goal_val, onboarding_state=STATE_NOTIFY_WHY)
            await q.edit_message_text('✅')
            await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, ctx)
            return
```

Заменить на:
```python
            goal_val = goal_map[data][0 if lang == 'ru' else 1]
            if data == 'goal_debt':
                # Запустить цикл сбора кредитов
                set_user(uid, goal=goal_val, onboarding_state=STATE_DEBT_COUNT)
                ctx.user_data['debt_target']  = 0
                ctx.user_data['debt_current'] = 0
                ctx.user_data['debt_temp']    = {}
                await q.edit_message_text('✅')
                await send_onboarding_step(chat_id, uid, STATE_DEBT_COUNT, ctx)
            else:
                set_user(uid, goal=goal_val, onboarding_state=STATE_NOTIFY_WHY)
                await q.edit_message_text('✅')
                await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, ctx)
            return
```

---

### Шаг 6: Обработать текстовый ввод для шагов долгов в `on_text()`

В функции `on_text()`, в блоке `if not done:` (онбординг), добавить после обработки STATE_NOTIFY_TIME:

```python
        elif state == STATE_DEBT_COUNT:
            try:
                count = int(text.strip())
                if count <= 0: raise ValueError
                set_user(uid, onboarding_state=STATE_DEBT_BANK)
                ctx.user_data['debt_target']  = count
                ctx.user_data['debt_current'] = 0
                ctx.user_data['debt_temp']    = {}
                await upd.message.reply_text('✅ Отлично! Начинаю собирать данные...' if lang == 'ru' else "✅ Zo'r! Ma'lumotlarni to'playapman...")
                await send_onboarding_step(chat_id, uid, STATE_DEBT_BANK, ctx)
            except:
                await upd.message.reply_text('❌ Напиши число, например: *3*' if lang == 'ru' else "❌ Raqam yozing, masalan: *3*", parse_mode='Markdown')
            return

        elif state == STATE_DEBT_BANK:
            ctx.user_data.setdefault('debt_temp', {})['bank'] = text
            set_user(uid, onboarding_state=STATE_DEBT_AMT)
            await send_onboarding_step(chat_id, uid, STATE_DEBT_AMT, ctx)
            return

        elif state == STATE_DEBT_AMT:
            try:
                amt = float(text.replace(' ', '').replace(',', '.'))
                ctx.user_data['debt_temp']['amount'] = amt
                set_user(uid, onboarding_state=STATE_DEBT_RATE)
                await send_onboarding_step(chat_id, uid, STATE_DEBT_RATE, ctx)
            except:
                await upd.message.reply_text('❌ Напиши число' if lang == 'ru' else '❌ Raqam yozing', parse_mode='Markdown')
            return

        elif state == STATE_DEBT_RATE:
            try:
                rate = float(text.replace('%', '').replace(' ', '').replace(',', '.'))
                ctx.user_data['debt_temp']['rate'] = rate
                set_user(uid, onboarding_state=STATE_DEBT_MONTHLY)
                await send_onboarding_step(chat_id, uid, STATE_DEBT_MONTHLY, ctx)
            except:
                await upd.message.reply_text('❌ Напиши число, например: *24*' if lang == 'ru' else "❌ Raqam yozing, masalan: *24*", parse_mode='Markdown')
            return

        elif state == STATE_DEBT_MONTHLY:
            try:
                monthly = float(text.replace(' ', '').replace(',', '.'))
                ctx.user_data['debt_temp']['monthly_payment'] = monthly
                set_user(uid, onboarding_state=STATE_DEBT_DEADLINE)
                await send_onboarding_step(chat_id, uid, STATE_DEBT_DEADLINE, ctx)
            except:
                await upd.message.reply_text('❌ Напиши число' if lang == 'ru' else '❌ Raqam yozing', parse_mode='Markdown')
            return

        elif state == STATE_DEBT_DEADLINE:
            ctx.user_data['debt_temp']['deadline'] = text
            # Сохранить кредит в БД
            d = ctx.user_data.get('debt_temp', {})
            with get_conn() as conn:
                with conn.cursor() as c:
                    c.execute(
                        'INSERT INTO debts(user_id, bank, amount, rate, monthly_payment, deadline) VALUES(%s,%s,%s,%s,%s,%s)',
                        (uid, d.get('bank', '?'), d.get('amount', 0), d.get('rate', 0),
                         d.get('monthly_payment', 0), d.get('deadline', '?'))
                    )
                conn.commit()
            current = ctx.user_data.get('debt_current', 0) + 1
            target  = ctx.user_data.get('debt_target', 1)
            ctx.user_data['debt_current'] = current
            ctx.user_data['debt_temp']    = {}
            if current < target:
                set_user(uid, onboarding_state=STATE_DEBT_BANK)
                await upd.message.reply_text(
                    f'✅ Записал!\n\n▶️ Следующий кредит ({current + 1} из {target})...'
                    if lang == 'ru' else
                    f"✅ Yozib oldim!\n\n▶️ Keyingi kredit ({current + 1} / {target})..."
                )
                await send_onboarding_step(chat_id, uid, STATE_DEBT_BANK, ctx)
            else:
                await upd.message.reply_text(
                    '✅ Все кредиты записаны!\n\n⏳ Анализирую и создаю стратегию...'
                    if lang == 'ru' else
                    "✅ Barcha kreditlar yozildi!\n\n⏳ Tahlil qilyapman va strategiya tuzmoqdaman..."
                )
                await generate_debt_strategy(uid, lang, chat_id, ctx)
            return
```

---

### Шаг 7: Добавить команду `/debts`

Вставить после функции `generate_debt_strategy()`:

```python
async def cmd_debts(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(
                'SELECT id, bank, amount, rate, monthly_payment, deadline FROM debts WHERE user_id=%s ORDER BY rate DESC',
                (uid,)
            )
            debts = c.fetchall()

    if not debts:
        msg = ('🎉 *У тебя нет активных кредитов!*\n\nОтлично! Держи эту планку! 💪'
               if lang == 'ru' else
               "🎉 *Sizda faol kreditlar yo'q!*\n\nAjoyib! Shunday davom eting! 💪")
        await upd.message.reply_text(msg, parse_mode='Markdown')
        return

    total = sum(d[2] for d in debts)
    total_monthly = sum(d[4] for d in debts)
    msg = (f"💳 *Твои кредиты ({len(debts)} шт):*\n\n"
           if lang == 'ru' else
           f"💳 *Sizning kreditlaringiz ({len(debts)} ta):*\n\n")

    for i, (id_, bank, amt, rate, monthly, deadline) in enumerate(debts, 1):
        msg += (f"{i}. *{bank}*\n"
                f"   Остаток: `{uzs(amt)}`\n"
                f"   Ставка: {rate}% | Платёж: `{uzs(monthly)}`/мес\n"
                f"   Срок: {deadline}\n\n")

    total_lbl   = 'Всего долгов'   if lang == 'ru' else 'Jami qarzlar'
    pay_lbl     = 'Платежей'       if lang == 'ru' else 'To\'lovlar'
    advice_note = '💡 _Используй /advice для стратегии погашения_' if lang == 'ru' else "💡 _/advice buyrug'i bilan to'lash strategiyasini oling_"

    msg += (f"📊 *ИТОГО:*\n{total_lbl}: `{uzs(total)}`\n{pay_lbl}: `{uzs(total_monthly)}`/мес\n\n{advice_note}")

    add_lbl   = '➕ Добавить кредит'    if lang == 'ru' else "➕ Kredit qo'shish"
    pay_btn   = '✅ Отметить платёж'    if lang == 'ru' else "✅ To'lovni belgilash"
    close_btn = '🎉 Закрыть кредит'    if lang == 'ru' else "🎉 Kreditni yopish"
    kb = [
        [InlineKeyboardButton(add_lbl,   callback_data='debt_add')],
        [InlineKeyboardButton(pay_btn,   callback_data='debt_pay')],
        [InlineKeyboardButton(close_btn, callback_data='debt_close')],
    ]
    await upd.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
```

---

### Шаг 8: Добавить callback-обработчики для управления долгами в `on_callback()`

В блоке `# ─── MAIN CALLBACKS ───` в `on_callback()` добавить:

```python
    # ─── DEBT MANAGEMENT ───
    if data == 'debt_add':
        ctx.user_data['debt_target']  = 1
        ctx.user_data['debt_current'] = 0
        ctx.user_data['debt_temp']    = {}
        ctx.user_data['debt_mode']    = 'add'
        set_user(uid, onboarding_state=STATE_DEBT_BANK)
        await q.answer('➕ Добавляем кредит...' if lang == 'ru' else "➕ Kredit qo'shilmoqda...")
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_DEBT_BANK, ctx)
        return

    if data == 'debt_pay':
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute('SELECT id, bank, amount FROM debts WHERE user_id=%s ORDER BY rate DESC', (uid,))
                debts = c.fetchall()
        if not debts:
            await q.answer('❌ Нет кредитов!' if lang == 'ru' else "❌ Kreditlar yo'q!"); return
        kb = [[InlineKeyboardButton(f"{bank} ({uzs(amt)})", callback_data=f'pay_{id_}')]
              for id_, bank, amt in debts]
        kb.append([InlineKeyboardButton('← Отмена' if lang == 'ru' else '← Bekor qilish', callback_data='debts_cancel')])
        await q.edit_message_text(
            '✅ *Какой кредит платишь?*' if lang == 'ru' else "✅ *Qaysi kreditni to'layapsiz?*",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )
        return

    if data.startswith('pay_'):
        debt_id = int(data[4:])
        ctx.user_data['paying_debt_id'] = debt_id
        set_user(uid, onboarding_state='debt_payment')
        await q.edit_message_text(
            '💰 *Сколько заплатил?*\n\nНапиши сумму платежа:' if lang == 'ru'
            else "💰 *Qancha to'ladingiz?*\n\nTo'lov summasini yozing:",
            parse_mode='Markdown'
        )
        return

    if data == 'debt_close':
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute('SELECT id, bank, amount FROM debts WHERE user_id=%s', (uid,))
                debts = c.fetchall()
        if not debts:
            await q.answer('❌ Нет кредитов!' if lang == 'ru' else "❌ Kreditlar yo'q!"); return
        kb = [[InlineKeyboardButton(f"🎉 {bank}", callback_data=f'close_{id_}')]
              for id_, bank, amt in debts]
        kb.append([InlineKeyboardButton('← Отмена' if lang == 'ru' else '← Bekor qilish', callback_data='debts_cancel')])
        await q.edit_message_text(
            '🎉 *Какой кредит закрыл полностью?*' if lang == 'ru' else "🎉 *Qaysi kreditni to'liq yopdingiz?*",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )
        return

    if data.startswith('close_'):
        debt_id = int(data[6:])
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute('SELECT bank, amount FROM debts WHERE id=%s', (debt_id,))
                result = c.fetchone()
                if result:
                    bank, amount = result
                    c.execute('DELETE FROM debts WHERE id=%s', (debt_id,))
            conn.commit()
        await q.answer('🎉 Поздравляю!' if lang == 'ru' else '🎉 Tabriklayman!')
        await q.edit_message_text(
            f"🎉🎉🎉 *КРАСАВА!!!*\n\nТы закрыл кредит *{bank}* на {uzs(amount)}!\n\nЭто ОГРОМНОЕ достижение! 💪💎\n\n_Используй /debts чтобы посмотреть остальные._"
            if lang == 'ru' else
            f"🎉🎉🎉 *AJOYIB!!!*\n\n*{bank}* kreditini {uzs(amount)} ga yopdingiz!\n\nBu KATTA yutuq! 💪💎\n\n_/debts bilan qolganlarni ko'ring._",
            parse_mode='Markdown'
        )
        return

    if data == 'debts_cancel':
        try: await q.message.delete()
        except: pass
        return
```

### Шаг 8б: Обработать платёж по кредиту в `on_text()`

В `on_text()`, в блоке `# ─── MAIN BOT LOGIC ───`, ПОСЛЕ проверки STATE_BUG_REPORT (если есть), добавить:

```python
    # Ввод суммы платежа по кредиту
    if state == 'debt_payment':
        try:
            payment = float(text.replace(' ', '').replace(',', '.'))
            debt_id = ctx.user_data.get('paying_debt_id')
            with get_conn() as conn:
                with conn.cursor() as c:
                    c.execute('SELECT amount, bank FROM debts WHERE id=%s', (debt_id,))
                    row = c.fetchone()
                    if row:
                        old_amt, bank = row
                        new_amt = old_amt - payment
                        if new_amt <= 0:
                            c.execute('DELETE FROM debts WHERE id=%s', (debt_id,))
                            reply = (f"🎉🎉🎉 *ПОЗДРАВЛЯЮ!!!*\n\nТы полностью закрыл кредит *{bank}*!\n\nЭто огромный шаг к финансовой свободе! 💪\n\n_/debts для оставшихся кредитов_"
                                     if lang == 'ru' else
                                     f"🎉🎉🎉 *TABRIKLAYMAN!!!*\n\n*{bank}* kreditini to'liq yopdingiz!\n\nBu moliyaviy erkinlikka katta qadam! 💪\n\n_/debts — qolgan kreditlar_")
                        else:
                            c.execute('UPDATE debts SET amount=%s WHERE id=%s', (new_amt, debt_id))
                            reply = (f"✅ *Платёж записан!*\n\n{bank}:\nБыло: `{uzs(old_amt)}`\nСтало: `{uzs(new_amt)}`\n\n💪 Так держать!"
                                     if lang == 'ru' else
                                     f"✅ *To'lov yozildi!*\n\n{bank}:\nAvval: `{uzs(old_amt)}`\nEndi: `{uzs(new_amt)}`\n\n💪 Davom eting!")
                    else:
                        reply = '❌ Кредит не найден' if lang == 'ru' else "❌ Kredit topilmadi"
                conn.commit()
            set_user(uid, onboarding_state=STATE_DONE)
            ctx.user_data.pop('paying_debt_id', None)
            await upd.message.reply_text(reply, parse_mode='Markdown')
        except:
            await upd.message.reply_text('❌ Напиши число' if lang == 'ru' else '❌ Raqam yozing')
        return
```

### Шаг 9: Зарегистрировать команду `/debts` в `main()`

```python
    app.add_handler(CommandHandler('debts', cmd_debts))
```

### Шаг 10: Добавить `/debts` в список команд бота в `setup_bot_ui()`

```python
        BotCommand('debts', '💳 Управление кредитами'),
```

---

## 🧪 Как проверить
1. `/start` → выбрать язык → ввести имя → ... → выбрать "💳 Закрыть долги/кредиты"
2. Должен спросить "Сколько кредитов?" → написать "2"
3. Для каждого кредита: банк → сумма → ставка → платёж → срок
4. После всех кредитов — AI генерирует стратегию погашения
5. Затем продолжается онбординг (вопрос про уведомления)
6. `/debts` — показывает список кредитов с кнопками
7. "✅ Отметить платёж" → выбрать кредит → ввести сумму → остаток уменьшается
8. "🎉 Закрыть кредит" → поздравление
9. "➕ Добавить кредит" → форма добавления нового

## ⚠️ Важно
- `ctx.user_data['debt_temp']` — временное хранилище данных текущего кредита
- `ctx.user_data['debt_target']` — сколько кредитов нужно ввести
- `ctx.user_data['debt_current']` — сколько уже введено
- Голосовой ввод для шагов долгов НЕ включён в эту задачу (можно добавить отдельно)
- После добавления через `debt_add` — нужно сбросить `onboarding_state` в `STATE_DONE` (это происходит автоматически через `generate_debt_strategy` или при завершении цикла)
- Если `debt_mode='add'` (не онбординг), после сохранения одного кредита — показать `/debts` вместо продолжения онбординга
