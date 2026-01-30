import os
import json
import sqlite3
from datetime import datetime, time
from flask import Flask, request, jsonify
import requests
from threading import Thread

# Telegram Bot API
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')  # ID чата твоего брата

# WhatsApp Business API (через twilio.com - бесплатный sandbox)
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.getenv('TWILIO_WHATSAPP_NUMBER')  # формат: whatsapp:+14155238886

app = Flask(__name__)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('bot_settings.db')
    c = conn.cursor()
    
    # Таблица настроек
    c.execute('''CREATE TABLE IF NOT EXISTS settings
                 (key TEXT PRIMARY KEY, value TEXT)''')
    
    # Таблица автоответов
    c.execute('''CREATE TABLE IF NOT EXISTS auto_replies
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  keyword TEXT UNIQUE,
                  response TEXT)''')
    
    # Таблица статистики
    c.execute('''CREATE TABLE IF NOT EXISTS messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  phone TEXT,
                  message TEXT,
                  timestamp TEXT)''')
    
    # Установка значений по умолчанию
    c.execute("INSERT OR IGNORE INTO settings VALUES ('bot_active', 'true')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('greeting', 'Здравствуйте! Спасибо за сообщение.')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('work_start', '09:00')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('work_end', '18:00')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('working_days', '1,2,3,4,5')")  # Пн-Пт
    c.execute("INSERT OR IGNORE INTO settings VALUES ('after_hours_msg', 'Мы вне рабочего времени. Ответим в рабочие часы.')")
    
    conn.commit()
    conn.close()

# Функции работы с БД
def get_setting(key):
    conn = sqlite3.connect('bot_settings.db')
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def set_setting(key, value):
    conn = sqlite3.connect('bot_settings.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def add_auto_reply(keyword, response):
    conn = sqlite3.connect('bot_settings.db')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO auto_replies VALUES (NULL, ?, ?)", (keyword.lower(), response))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def get_auto_reply(message):
    conn = sqlite3.connect('bot_settings.db')
    c = conn.cursor()
    c.execute("SELECT response FROM auto_replies")
    replies = c.fetchall()
    conn.close()
    
    message_lower = message.lower()
    for reply in replies:
        if reply[0] in message_lower:
            return reply[1]
    return None

def list_auto_replies():
    conn = sqlite3.connect('bot_settings.db')
    c = conn.cursor()
    c.execute("SELECT keyword, response FROM auto_replies")
    replies = c.fetchall()
    conn.close()
    return replies

def delete_auto_reply(keyword):
    conn = sqlite3.connect('bot_settings.db')
    c = conn.cursor()
    c.execute("DELETE FROM auto_replies WHERE keyword=?", (keyword.lower(),))
    conn.commit()
    conn.close()

def save_message(phone, message):
    conn = sqlite3.connect('bot_settings.db')
    c = conn.cursor()
    c.execute("INSERT INTO messages VALUES (NULL, ?, ?, ?)", 
              (phone, message, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect('bot_settings.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM messages")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT phone) FROM messages")
    unique_users = c.fetchone()[0]
    conn.close()
    return total, unique_users

# Проверка рабочего времени
def is_working_hours():
    now = datetime.now()
    current_day = now.weekday() + 1  # 1=Пн, 7=Вс
    
    working_days = get_setting('working_days').split(',')
    if str(current_day) not in working_days:
        return False
    
    work_start = datetime.strptime(get_setting('work_start'), '%H:%M').time()
    work_end = datetime.strptime(get_setting('work_end'), '%H:%M').time()
    current_time = now.time()
    
    return work_start <= current_time <= work_end

# Отправка сообщения в Telegram
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    requests.post(url, data=data)

# Отправка WhatsApp сообщения
def send_whatsapp_message(to_number, message):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    data = {
        'From': TWILIO_WHATSAPP_NUMBER,
        'To': to_number,
        'Body': message
    }
    requests.post(url, data=data, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))

# Webhook для WhatsApp сообщений
@app.route('/whatsapp', methods=['POST'])
def whatsapp_webhook():
    incoming_msg = request.values.get('Body', '').strip()
    from_number = request.values.get('From', '')
    
    # Сохранение сообщения
    save_message(from_number, incoming_msg)
    
    # Уведомление в Telegram
    send_telegram_message(f"📱 <b>Новое сообщение WhatsApp</b>\n"
                         f"От: {from_number}\n"
                         f"Текст: {incoming_msg}")
    
    # Проверка активности бота
    if get_setting('bot_active') != 'true':
        return '', 200
    
    # Проверка рабочего времени
    if not is_working_hours():
        response = get_setting('after_hours_msg')
        send_whatsapp_message(from_number, response)
        return '', 200
    
    # Поиск автоответа по ключевому слову
    auto_response = get_auto_reply(incoming_msg)
    if auto_response:
        send_whatsapp_message(from_number, auto_response)
        return '', 200
    
    # Отправка приветствия по умолчанию
    greeting = get_setting('greeting')
    send_whatsapp_message(from_number, greeting)
    
    return '', 200

# Webhook для Telegram бота
@app.route('/telegram', methods=['POST'])
def telegram_webhook():
    update = request.get_json()
    
    if 'message' not in update:
        return '', 200
    
    message = update['message']
    chat_id = message['chat']['id']
    text = message.get('text', '')
    
    # Проверка, что это сообщение от твоего брата
    if str(chat_id) != TELEGRAM_CHAT_ID:
        return '', 200
    
    # Обработка команд
    if text.startswith('/start'):
        response = """🤖 <b>WhatsApp Бот - Панель управления</b>

Доступные команды:

📝 <b>Автоответы:</b>
/add [ключевое_слово] | [ответ] - добавить автоответ
/list - список всех автоответов
/delete [ключевое_слово] - удалить автоответ

⚙️ <b>Настройки:</b>
/greeting [текст] - изменить приветствие
/schedule - настроить рабочее время
/on - включить бота
/off - выключить бота

📊 <b>Статистика:</b>
/stats - посмотреть статистику

💡 <b>Примеры:</b>
/add цена | Наши цены начинаются от 1000₽
/greeting Добро пожаловать в наш магазин!"""
        
        send_telegram_message(response)
    
    elif text.startswith('/add '):
        parts = text[5:].split('|')
        if len(parts) == 2:
            keyword = parts[0].strip()
            response = parts[1].strip()
            if add_auto_reply(keyword, response):
                send_telegram_message(f"✅ Автоответ добавлен!\nКлюч: {keyword}\nОтвет: {response}")
            else:
                send_telegram_message(f"❌ Ключевое слово '{keyword}' уже существует. Удалите его сначала.")
        else:
            send_telegram_message("❌ Неверный формат. Используй:\n/add ключевое_слово | ответ")
    
    elif text.startswith('/list'):
        replies = list_auto_replies()
        if replies:
            response = "📋 <b>Список автоответов:</b>\n\n"
            for keyword, reply in replies:
                response += f"🔹 <b>{keyword}</b>\n   → {reply}\n\n"
        else:
            response = "Автоответов пока нет."
        send_telegram_message(response)
    
    elif text.startswith('/delete '):
        keyword = text[8:].strip()
        delete_auto_reply(keyword)
        send_telegram_message(f"✅ Автоответ '{keyword}' удален")
    
    elif text.startswith('/greeting '):
        new_greeting = text[10:].strip()
        set_setting('greeting', new_greeting)
        send_telegram_message(f"✅ Приветствие изменено на:\n{new_greeting}")
    
    elif text.startswith('/on'):
        set_setting('bot_active', 'true')
        send_telegram_message("✅ Бот включен")
    
    elif text.startswith('/off'):
        set_setting('bot_active', 'false')
        send_telegram_message("✅ Бот выключен")
    
    elif text.startswith('/stats'):
        total, unique = get_stats()
        bot_status = "🟢 Включен" if get_setting('bot_active') == 'true' else "🔴 Выключен"
        response = f"""📊 <b>Статистика бота</b>

Статус: {bot_status}
Всего сообщений: {total}
Уникальных пользователей: {unique}
Автоответов: {len(list_auto_replies())}"""
        send_telegram_message(response)
    
    elif text.startswith('/schedule'):
        response = """⏰ <b>Настройка рабочего времени</b>

Используй команды:
/set_hours [начало] [конец] - например: /set_hours 09:00 18:00
/set_days [дни] - например: /set_days 1,2,3,4,5 (Пн-Пт)
/after_hours [текст] - сообщение вне рабочего времени

Дни недели: 1=Пн, 2=Вт, 3=Ср, 4=Чт, 5=Пт, 6=Сб, 7=Вс"""
        send_telegram_message(response)
    
    elif text.startswith('/set_hours '):
        parts = text[11:].split()
        if len(parts) == 2:
            set_setting('work_start', parts[0])
            set_setting('work_end', parts[1])
            send_telegram_message(f"✅ Рабочее время: {parts[0]} - {parts[1]}")
        else:
            send_telegram_message("❌ Формат: /set_hours 09:00 18:00")
    
    elif text.startswith('/set_days '):
        days = text[10:].strip()
        set_setting('working_days', days)
        send_telegram_message(f"✅ Рабочие дни установлены: {days}")
    
    elif text.startswith('/after_hours '):
        msg = text[13:].strip()
        set_setting('after_hours_msg', msg)
        send_telegram_message(f"✅ Сообщение вне рабочего времени:\n{msg}")
    
    else:
        send_telegram_message("❓ Неизвестная команда. Используй /start для списка команд.")
    
    return '', 200

# Главная страница
@app.route('/')
def home():
    return "WhatsApp Bot is running! 🤖"

if __name__ == '__main__':
    init_db()
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
