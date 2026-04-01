# TASK 07 — Полировка UX (мотивации + эмоции)

## 📋 Задача
Добавить случайные мотивационные сообщения при записи транзакций (10% вероятность).
Это делает бот живым и вдохновляющим.

## 📁 Файл для изменения
`bot.py` — только этот файл.

## ✅ Что нужно сделать

### Шаг 1: Добавить словарь мотиваций

Найти блок `# ────────────────────────── FORMATTERS ──────────────────────────────` и ПЕРЕД ним добавить:

```python
# ────────────────────────── MOTIVATIONS ──────────────────────────
import random as _random

MOTIVATIONS = {
    'ru': [
        "\n\n💪 *Молодец!* Каждая запись приближает к цели!",
        "\n\n🔥 Так держать! Твоя финансовая дисциплина впечатляет!",
        "\n\n⭐ Отлично! Ты контролируешь свои финансы — это редкость!",
        "\n\n💎 Круто! Продолжай в том же духе — результат не заставит ждать!",
        "\n\n🚀 +1 к финансовой осознанности! Ты движешься к своей цели!",
        "\n\n✨ Именно такие маленькие шаги складываются в большие перемены!",
    ],
    'uz': [
        "\n\n💪 *Ajoyib!* Har bir yozuv maqsadga yaqinlashtiradi!",
        "\n\n🔥 Davom eting! Moliyaviy intizomingiz ta'sirli!",
        "\n\n⭐ Zo'r! Moliyangizni nazorat qilish — bu kamdan-kam mahorat!",
        "\n\n💎 Ajoyib! Shunday davom eting — natija kutdirmaydi!",
        "\n\n🚀 Moliyaviy ongingiz oshmoqda! Maqsadingizga yaqinlashyapsiz!",
        "\n\n✨ Aynan shunday kichik qadamlar katta o'zgarishlarga olib keladi!",
    ]
}

def maybe_motivate(lang: str) -> str:
    """Вернуть случайную мотивацию с вероятностью 10%, или пустую строку."""
    if _random.random() < 0.10:
        return _random.choice(MOTIVATIONS.get(lang, MOTIVATIONS['ru']))
    return ''
```

### Шаг 2: Использовать мотивацию при записи транзакции в `on_text()`

Найти в `on_text()` строку:
```python
    await msg.edit_text(fmt_tx_msg(parsed, lang, rates), parse_mode='Markdown')
```
Заменить на:
```python
    await msg.edit_text(fmt_tx_msg(parsed, lang, rates) + maybe_motivate(lang), parse_mode='Markdown')
```

### Шаг 3: Использовать мотивацию при записи через голос в `on_voice()`

Найти в `on_voice()` строку:
```python
    await msg.edit_text(f'🎤 _{transcript}_\n\n' + fmt_tx_msg(parsed, lang, rates), parse_mode='Markdown')
```
Заменить на:
```python
    await msg.edit_text(f'🎤 _{transcript}_\n\n' + fmt_tx_msg(parsed, lang, rates) + maybe_motivate(lang), parse_mode='Markdown')
```

### Шаг 4: Использовать мотивацию при записи через фото в `on_photo()`

Найти в `on_photo()` строку:
```python
    await msg.edit_text(fmt_tx_msg(parsed, lang, rates), parse_mode='Markdown')
```
Заменить на:
```python
    await msg.edit_text(fmt_tx_msg(parsed, lang, rates) + maybe_motivate(lang), parse_mode='Markdown')
```

### Шаг 5: Улучшить приветственное сообщение после онбординга

Найти в словаре `T['ru']` ключ `'welcome_done'` и обновить значение:

```python
        'welcome_done'     : ('🎉 *{name}, добро пожаловать в Finora!*\n\n'
                              'Теперь я знаю тебя и буду помогать управлять деньгами 💎\n\n'
                              '*Как это работает:*\n'
                              '📝 Напиши что потратил/заработал — я запишу\n'
                              '🎤 Скажи голосом — я пойму\n'
                              '📷 Отправь фото чека — прочитаю сам\n\n'
                              '*Команды:*\n'
                              '/stats — 📊 Статистика\n'
                              '/history — 📋 История\n'
                              '/advice — 🤖 Персональный совет\n'
                              '/rate — 💱 Курс валют\n'
                              '/settings — ⚙️ Настройки\n'
                              '/bug — 🐛 Сообщить об ошибке\n'
                              '/help — ❓ Помощь\n\n'
                              '_Просто напиши мне — и мы начнём!_ 🚀'),
```

Найти в словаре `T['uz']` ключ `'welcome_done'` и обновить:

```python
        'welcome_done'     : ('🎉 *{name}, Finoraga xush kelibsiz!*\n\n'
                              'Endi sizni tanidim va pul boshqarishda yordam beraman 💎\n\n'
                              '*Qanday ishlaydi:*\n'
                              '📝 Nima sarflaganingizni yozing — yozib olaman\n'
                              '🎤 Ovoz bilan ayting — tushunaman\n'
                              '📷 Chek rasmini yuboring — o\'zim o\'qiyman\n\n'
                              '*Buyruqlar:*\n'
                              '/stats — 📊 Statistika\n'
                              '/history — 📋 Tarix\n'
                              '/advice — 🤖 Shaxsiy maslahat\n'
                              '/rate — 💱 Valyuta kursi\n'
                              '/settings — ⚙️ Sozlamalar\n'
                              '/bug — 🐛 Xatolik haqida xabar\n'
                              '/help — ❓ Yordam\n\n'
                              '_Menga yozing — boshlaymiz!_ 🚀'),
```

## 🧪 Как проверить
1. Записать 10-15 транзакций — примерно в 1-2 случаях должна появиться мотивационная фраза
2. Она добавляется в конце обычного сообщения о записи
3. Мотивация отображается жирным (звёздочки)
4. Пройти онбординг до конца — новое приветствие с /bug командой в списке

## ⚠️ Важно
- `import random` уже может быть в файле — если есть, НЕ добавлять повторно. Но в нашем случае используем `import random as _random` чтобы не конфликтовать
- Проверить что `_random` не конфликтует с существующими импортами
- 10% (0.10) — это примерно 1 раз из 10 записей. Можно поменять на 0.15 (15%) если хочется чаще
