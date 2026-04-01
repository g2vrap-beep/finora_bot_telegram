# TASK 01 — Валидация типов файлов

## 📋 Задача
Добавить обработчики для неподдерживаемых типов сообщений: видео, документы, стикеры, аудио файлы.
Сейчас бот их игнорирует или выдаёт ошибку. Нужно отвечать понятным дружеским сообщением.

## 📁 Файл для изменения
`bot.py` — только этот файл.

## ✅ Что нужно сделать

### Шаг 1: Добавить 4 новые async функции-handler после функции `on_voice`

Вставить ПОСЛЕ блока `async def on_voice(...)` и ДО блока `async def on_callback(...)`:

```python
async def on_video(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    if lang == 'ru':
        msg = ("❌ *Видео не поддерживается!*\n\n"
               "Я понимаю только:\n"
               "📷 Фото чека — для записи расходов\n"
               "🎤 Голосовое сообщение\n"
               "✍️ Текст\n\n"
               "Отправь *фото чека* вместо видео! 📸")
    else:
        msg = ("❌ *Video qo'llab-quvvatlanmaydi!*\n\n"
               "Men faqat tushunaman:\n"
               "📷 Chek rasmi — xarajatlarni yozish uchun\n"
               "🎤 Ovozli xabar\n"
               "✍️ Matn\n\n"
               "Video o'rniga *chek rasmini* yuboring! 📸")
    await upd.message.reply_text(msg, parse_mode='Markdown')

async def on_document(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    if lang == 'ru':
        msg = ("❌ *Файлы не поддерживаются!*\n\n"
               "Я понимаю только:\n"
               "📷 Фото чека\n"
               "🎤 Голосовые сообщения\n"
               "✍️ Текст\n\n"
               "💡 _Если это чек — отправь как фото, а не файл!_")
    else:
        msg = ("❌ *Fayllar qo'llab-quvvatlanmaydi!*\n\n"
               "Men faqat tushunaman:\n"
               "📷 Chek rasmi\n"
               "🎤 Ovozli xabar\n"
               "✍️ Matn\n\n"
               "💡 _Agar bu chek bo'lsa — fayl emas, rasm sifatida yuboring!_")
    await upd.message.reply_text(msg, parse_mode='Markdown')

async def on_sticker(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    import random
    responses = {
        'ru': [
            "😄 Классный стикер! Но я не умею их обрабатывать 😅\n\n💡 Чем помочь по финансам?",
            "👍 Хаха, понял! 😊\n\n💰 Может запишем какую-нибудь трату?",
            "🙈 Стикеры люблю, но трату по ним записать не могу 😅",
        ],
        'uz': [
            "😄 Zo'r stiker! Lekin ularni qayta ishlay olmayman 😅\n\n💡 Moliya bo'yicha yordam kerakmi?",
            "👍 Haha, tushundim! 😊\n\n💰 Biror xarajatni yozib qo'yaymi?",
            "🙈 Stikerlarni yoqtiraman, lekin ular orqali xarajat yoza olmayman 😅",
        ]
    }
    await upd.message.reply_text(random.choice(responses.get(lang, responses['ru'])))

async def on_audio(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    if lang == 'ru':
        msg = ("❌ *Аудио файлы не поддерживаются!*\n\n"
               "Используй 🎤 *голосовые сообщения* вместо аудио файлов!\n\n"
               "💡 Голосовое сообщение — это кнопка микрофона 🎤 справа от поля ввода.")
    else:
        msg = ("❌ *Audio fayllar qo'llab-quvvatlanmaydi!*\n\n"
               "Audio fayl o'rniga 🎤 *ovozli xabar* ishlating!\n\n"
               "💡 Ovozli xabar — yozish maydonining o'ng tomonidagi mikrofon tugmasi 🎤")
    await upd.message.reply_text(msg, parse_mode='Markdown')
```

### Шаг 2: Зарегистрировать handlers в функции `main()`

Найти блок с `app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))` и ПОСЛЕ него добавить:

```python
    app.add_handler(MessageHandler(filters.VIDEO, on_video))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.Sticker.ALL, on_sticker))
    app.add_handler(MessageHandler(filters.AUDIO, on_audio))
```

## 🧪 Как проверить
1. Отправить боту видео → должно прийти сообщение "Видео не поддерживается"
2. Отправить документ → "Файлы не поддерживаются"  
3. Отправить стикер → случайный дружеский ответ
4. Отправить аудио файл (не голосовое) → "Аудио не поддерживается"
5. Обычный текст и голосовое — работают как раньше

## ⚠️ Важно
- НЕ трогать `filters.VOICE` — это уже обрабатывается `on_voice`
- `filters.Document.ALL` и `filters.AUDIO` — разные фильтры
- Ответы на оба языка (ru и uz) обязательны
