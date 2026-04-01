# TASK 02 — Курсы валют с dollaruz.net

## 📋 Задача
Обновить функцию `get_rates()` — получать реальные рыночные курсы (покупка/продажа) с сайта dollaruz.net.
Сейчас используется только официальный курс ЦБ (cbu.uz) без разделения на покупку/продажу.

## 📁 Файл для изменения
`bot.py` — только этот файл.

## ✅ Что нужно сделать

### Шаг 1: Заменить функцию `get_rates()`

Найти СУЩЕСТВУЮЩУЮ функцию:
```python
def get_rates() -> dict:
    try:
        data = requests.get('https://cbu.uz/oz/arkhiv-kursov-valyut/json/', timeout=8).json()
        return {d['Ccy']: {'rate': float(d['Rate']), 'diff': float(d.get('Diff', 0))}
                for d in data if d.get('Ccy') in ('USD', 'EUR', 'RUB')}
    except:
        return {}
```

Заменить на:
```python
def get_rates() -> dict:
    """Получить курсы с dollaruz.net (реальные рыночные). Fallback: CBU."""
    # 1️⃣ Попытка получить реальные курсы с dollaruz.net
    try:
        resp = requests.get(
            'https://dollaruz.net/api/currency',
            timeout=8,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        if resp.status_code == 200:
            data = resp.json()
            result = {}
            for ccy in ('USD', 'EUR', 'RUB'):
                entry = data.get(ccy, {})
                buy  = float(entry.get('buy', 0) or 0)
                sell = float(entry.get('sell', 0) or 0)
                if buy > 0 or sell > 0:
                    result[ccy] = {
                        'buy':  buy,
                        'sell': sell,
                        'avg':  round((buy + sell) / 2, 2) if buy and sell else (buy or sell),
                        'diff': 0,
                        'source': 'dollaruz'
                    }
            if result:
                return result
    except Exception as e:
        logger.warning(f'dollaruz.net error: {e}')

    # 2️⃣ Fallback: официальный курс ЦБ
    try:
        data = requests.get('https://cbu.uz/oz/arkhiv-kursov-valyut/json/', timeout=8).json()
        return {
            d['Ccy']: {
                'buy':  float(d['Rate']),
                'sell': float(d['Rate']),
                'avg':  float(d['Rate']),
                'diff': float(d.get('Diff', 0)),
                'source': 'cbu'
            }
            for d in data if d.get('Ccy') in ('USD', 'EUR', 'RUB')
        }
    except:
        return {}
```

### Шаг 2: Обновить функцию `cmd_rate()`

Найти существующую `async def cmd_rate(...)` и заменить её содержимое:

```python
async def cmd_rate(upd: Update, _):
    uid   = upd.effective_user.id
    lang  = get_lang(uid)
    rates = get_rates()
    if not rates:
        await upd.message.reply_text(tx(lang, 'rate_err')); return

    # Определить источник
    sample = next(iter(rates.values()), {})
    source = sample.get('source', 'cbu')
    source_label = 'dollaruz.net' if source == 'dollaruz' else 'ЦБ Узбекистана'

    header = f"💱 *Курс валют ({source_label})*\n\n" if lang == 'ru' else f"💱 *Valyuta kursi ({source_label})*\n\n"
    lines  = [header]

    flags  = {'USD': '🇺🇸', 'EUR': '🇪🇺', 'RUB': '🇷🇺'}

    for ccy, d in rates.items():
        flag = flags.get(ccy, '💱')
        buy  = d.get('buy', 0)
        sell = d.get('sell', 0)
        avg  = d.get('avg', 0)
        diff = d.get('diff', 0)

        if source == 'dollaruz' and buy and sell and buy != sell:
            buy_lbl  = 'Покупка'  if lang == 'ru' else 'Sotib olish'
            sell_lbl = 'Продажа'  if lang == 'ru' else 'Sotish'
            lines.append(
                f"{flag} *{ccy}*\n"
                f"  {buy_lbl}: `{uzs(buy)}`\n"
                f"  {sell_lbl}: `{uzs(sell)}`\n"
            )
        else:
            arrow = '🔺' if diff > 0 else ('🔻' if diff < 0 else '➡️')
            lines.append(f"{arrow} *{ccy}* = `{uzs(avg)}` ({diff:+.2f})\n")

    lines.append(f"\n_{tx(lang, 'updated')}: {datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}_")
    await upd.message.reply_text(''.join(lines), parse_mode='Markdown')
```

## 🧪 Как проверить
1. Написать боту `/rate`
2. Проверить: если dollaruz.net доступен — показывает "Покупка" и "Продажа"
3. Если dollaruz.net недоступен — показывает курс ЦБ как раньше (автоматический fallback)
4. Дата и время обновления показывается корректно

## ⚠️ Важно
- Функция `uzs(n)` уже существует — использовать её
- `logger.warning()` уже импортирован — использовать его
- `datetime.now(TZ)` — TZ уже определён как `ZoneInfo('Asia/Tashkent')`
- Если dollaruz.net изменит структуру API — fallback на CBU сработает автоматически
