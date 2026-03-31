# 🔍 Где найти URL проекта в Railway

## Способ 1: Через вкладку Settings (рекомендуется)

1. Открой **https://railway.app**
2. Войди в аккаунт
3. Найди свой проект **finora_bot_telegram** (или как ты его назвал)
4. **НАЖМИ НА ПРОЕКТ** (кликни по нему)
5. Слева увидишь список сервисов - **нажми на любой из них** (можешь на bot.py)
6. Вверху увидишь вкладки: **Metrics, Deployments, Settings, Variables...**
7. **НАЖМИ НА "Settings"** (Настройки)
8. Прокрути вниз до раздела **"Domains"** или **"Public Networking"**
9. Там будет кнопка **"Generate Domain"** (если домена еще нет)
   - Нажми эту кнопку!
10. Railway создаст домен типа: `finora-bot-production.up.railway.app`
11. **ВОТ ЭТО И ЕСТЬ ТВОЙ URL!** Скопируй его!

---

## Способ 2: Посмотреть в Variables (если уже есть)

1. Открой **https://railway.app**
2. Твой проект **finora_bot_telegram**
3. Вкладка **"Variables"** (слева)
4. Посмотри есть ли переменная `RAILWAY_PUBLIC_DOMAIN` или `RAILWAY_STATIC_URL`
5. Если есть - это твой URL!

---

## Способ 3: Посмотреть в Deployments

1. Открой проект в Railway
2. Вкладка **"Deployments"**
3. Последний деплой (самый верхний)
4. Справа будет иконка 🌐 или кнопка **"View Logs"**
5. Рядом может быть URL типа: `https://xxxxx.up.railway.app`

---

## 📝 Что делать с URL после того как нашел:

Скопируй URL (например: `https://finora-bot-production.up.railway.app`)

И добавь в Railway Variables:

```
Name: DASHBOARD_URL
Value: https://finora-bot-production.up.railway.app
```

(вставь СВОЙ URL, не копируй мой пример!)

---

## ❓ Если вообще нигде нет URL:

Значит Railway еще не создал публичный домен. Сделай так:

1. Открой проект
2. Выбери сервис (например bot.py)
3. Settings → Networking
4. Нажми **"Generate Domain"**
5. Railway создаст домен автоматически!

---

## 🎯 Итого:

**URL выглядит примерно так:**
- `https://finora-bot-production.up.railway.app`
- `https://твой-проект-123abc.up.railway.app`
- `https://что-то-случайное.railway.app`

**Просто найди его в Settings → Domains и скопируй!**

---

Нашел URL? Отлично! Добавь его в переменную `DASHBOARD_URL` и продолжай по инструкции в `КАК_ЗАПУСТИТЬ.md` 👍
