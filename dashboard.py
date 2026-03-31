#!/usr/bin/env python3
"""
💎 Finora Dashboard — Персональная финансовая панель
Веб-интерфейс с Telegram авторизацией
"""

import os, json, hashlib, hmac
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'finora-dashboard-secret-change-me')

BOT_TOKEN = os.getenv('BOT_TOKEN', '')
DATABASE_URL = os.getenv('DATABASE_URL', '')
DATABASE_PUBLIC_URL = os.getenv('DATABASE_PUBLIC_URL', '')
TZ = ZoneInfo('Asia/Tashkent')

def _normalize_pg_url(url: str) -> str:
    if url.startswith('postgres://'):
        return 'postgresql://' + url[len('postgres://'):]
    return url

def get_conn():
    if DATABASE_PUBLIC_URL:
        return psycopg2.connect(_normalize_pg_url(DATABASE_PUBLIC_URL))
    if DATABASE_URL:
        return psycopg2.connect(_normalize_pg_url(DATABASE_URL))
    raise ValueError('Set DATABASE_PUBLIC_URL or DATABASE_URL')

def verify_telegram_auth(auth_data: dict) -> bool:
    """Проверка данных от Telegram Login Widget"""
    check_hash = auth_data.get('hash')
    if not check_hash:
        return False
    
    auth_data = dict(auth_data)
    del auth_data['hash']
    
    data_check_arr = [f'{k}={v}' for k, v in sorted(auth_data.items())]
    data_check_string = '\n'.join(data_check_arr)
    
    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    hash_value = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    
    if hash_value != check_hash:
        return False
    
    # Проверка времени (данные действительны 24 часа)
    auth_date = int(auth_data.get('auth_date', 0))
    if datetime.now().timestamp() - auth_date > 86400:
        return False
    
    return True

def get_user_stats(user_id: int) -> dict:
    """Получить статистику пользователя"""
    month = datetime.now(TZ).strftime('%Y-%m')
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            # Общая статистика
            c.execute('''
                SELECT type, SUM(amount) as total 
                FROM transactions 
                WHERE user_id=%s 
                GROUP BY type
            ''', (user_id,))
            totals = {row['type']: float(row['total']) for row in c.fetchall()}
            
            # Статистика за месяц
            c.execute('''
                SELECT type, SUM(amount) as total 
                FROM transactions 
                WHERE user_id=%s 
                AND TO_CHAR(created_at AT TIME ZONE 'Asia/Tashkent', 'YYYY-MM')=%s
                GROUP BY type
            ''', (user_id, month))
            month_totals = {row['type']: float(row['total']) for row in c.fetchall()}
            
            # Топ категории расходов
            c.execute('''
                SELECT category, SUM(amount) as total
                FROM transactions
                WHERE user_id=%s AND type='exp'
                AND TO_CHAR(created_at AT TIME ZONE 'Asia/Tashkent', 'YYYY-MM')=%s
                GROUP BY category
                ORDER BY total DESC
                LIMIT 10
            ''', (user_id, month))
            categories = [dict(row) for row in c.fetchall()]
            
            # Данные для графика (последние 6 месяцев)
            c.execute('''
                SELECT 
                    TO_CHAR(created_at AT TIME ZONE 'Asia/Tashkent', 'YYYY-MM') as month,
                    type,
                    SUM(amount) as total
                FROM transactions
                WHERE user_id=%s
                AND created_at >= NOW() - INTERVAL '6 months'
                GROUP BY month, type
                ORDER BY month
            ''', (user_id,))
            monthly_data = {}
            for row in c.fetchall():
                m = row['month']
                if m not in monthly_data:
                    monthly_data[m] = {'inc': 0, 'exp': 0}
                monthly_data[m][row['type']] = float(row['total'])
            
            # Последние транзакции
            c.execute('''
                SELECT id, type, amount, description, category, currency, created_at
                FROM transactions
                WHERE user_id=%s
                ORDER BY created_at DESC
                LIMIT 50
            ''', (user_id,))
            transactions = [dict(row) for row in c.fetchall()]
            
            # Информация о пользователе
            c.execute('SELECT * FROM users WHERE user_id=%s', (user_id,))
            user_info = dict(c.fetchone() or {})
    
    return {
        'total_income': totals.get('inc', 0),
        'total_expense': totals.get('exp', 0),
        'balance': totals.get('inc', 0) - totals.get('exp', 0),
        'month_income': month_totals.get('inc', 0),
        'month_expense': month_totals.get('exp', 0),
        'month_balance': month_totals.get('inc', 0) - month_totals.get('exp', 0),
        'categories': categories,
        'monthly_data': monthly_data,
        'transactions': transactions,
        'user_info': user_info
    }

@app.route('/')
def index():
    """Главная страница - вход или дашборд"""
    if 'user_id' not in session:
        return render_template('login.html', bot_token=BOT_TOKEN)
    
    user_id = session['user_id']
    stats = get_user_stats(user_id)
    
    return render_template('dashboard.html', 
                         user_name=session.get('first_name', 'Пользователь'),
                         stats=stats)

@app.route('/auth/telegram', methods=['POST'])
def telegram_auth():
    """Обработка авторизации через Telegram"""
    auth_data = request.form.to_dict()
    
    if not verify_telegram_auth(auth_data):
        return jsonify({'error': 'Ошибка авторизации'}), 403
    
    # Сохраняем данные в сессию
    session['user_id'] = int(auth_data['id'])
    session['first_name'] = auth_data.get('first_name', '')
    session['last_name'] = auth_data.get('last_name', '')
    session['username'] = auth_data.get('username', '')
    session['photo_url'] = auth_data.get('photo_url', '')
    
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    """Выход из системы"""
    session.clear()
    return redirect(url_for('index'))

@app.route('/api/stats')
def api_stats():
    """API для получения статистики (для AJAX)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authorized'}), 401
    
    user_id = session['user_id']
    stats = get_user_stats(user_id)
    
    # Конвертируем datetime в строки для JSON
    for tx in stats['transactions']:
        if tx.get('created_at'):
            tx['created_at'] = tx['created_at'].isoformat()
    
    return jsonify(stats)

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
