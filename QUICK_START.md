# ⚡ Быстрый старт

## 1️⃣ Добавь переменные в Railway

```bash
GROQ_KEY=gsk_...              # Получи на https://console.groq.com/
DASHBOARD_URL=https://твой-проект.up.railway.app
FLASK_SECRET_KEY=random32chars  # Сгенерируй: python -c "import secrets; print(secrets.token_hex(32))"
```

## 2️⃣ Push на GitHub

```bash
cd finora_bot_telegram
git add .
git commit -m "✨ Voice onboarding + Dashboard"
git push origin main
```

## 3️⃣ Railway автодеплой

Railway сам:
- Установит зависимости
- Запустит бота (worker)
- Запустит дашборд (web)
- Выдаст публичный URL

## 4️⃣ Протестируй

1. Открой бота в Telegram
2. Напиши: `/start`
3. Скажи голосом: "Меня зовут Влад" 🎤
4. Продолжай регистрацию голосом!
5. После регистрации: `/dashboard` → открой веб-панель

---

✅ **Готово!** Теперь бот понимает голос в онбординге и есть красивый дашборд!

📚 Подробная инструкция: см. [DEPLOY.md](./DEPLOY.md)
