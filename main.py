#!/usr/bin/env python3
"""
Торговый бот для мониторинга криптовалют на MEXC
Версия: 2.0
"""

import os
import sys
from datetime import datetime
from flask import Flask
from threading import Thread
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла в самом начале
load_dotenv()

# Проверяем, что переменные загружены
print(f"DEBUG: TELEGRAM_TOKEN = {os.getenv('TELEGRAM_TOKEN')[:10]}..." if os.getenv('TELEGRAM_TOKEN') else "DEBUG: TELEGRAM_TOKEN не найден")
print(f"DEBUG: TELEGRAM_CHAT_ID = {os.getenv('TELEGRAM_CHAT_ID')}")

from logger import bot_logger
from config import config_manager
from telegram_bot import telegram_bot
from watchlist_manager import watchlist_manager

# Flask приложение для keep-alive
app = Flask(__name__)

@app.route('/')
def health_check():
    """Проверка работоспособности бота"""
    status = {
        'bot_running': telegram_bot.bot_running,
        'bot_mode': telegram_bot.bot_mode,
        'watchlist_size': watchlist_manager.size()
    }

    return f"""
    <html>
    <head><title>Trading Bot Status</title></head>
    <body>
        <h1>🤖 Trading Bot Status</h1>
        <p><strong>Status:</strong> {'🟢 Running' if status['bot_running'] else '🔴 Stopped'}</p>
        <p><strong>Mode:</strong> {status['bot_mode'] or 'None'}</p>
        <p><strong>Watchlist:</strong> {status['watchlist_size']} coins</p>
        <p><strong>Version:</strong> 2.0</p>
        <hr>
        <small>Last updated: {telegram_bot.last_message_time}</small>
    </body>
    </html>
    """

@app.route('/health')
def health():
    """Health check endpoint"""
    return {'status': 'ok', 'version': '2.0'}

def run_flask():
    """Запуск Flask сервера"""
    try:
        app.run(host='0.0.0.0', port=8080, debug=False)
    except Exception as e:
        bot_logger.error(f"Ошибка Flask сервера: {e}")

def keep_alive():
    """Поддержка работы сервера"""
    server_thread = Thread(target=run_flask, daemon=True)
    server_thread.start()
    bot_logger.info("Flask сервер запущен на порту 8080")

def validate_environment():
    """Проверка переменных окружения"""
    required_vars = ['TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID']
    missing_vars = []

    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)

    if missing_vars:
        bot_logger.error(f"Отсутствуют переменные окружения: {', '.join(missing_vars)}")
        print(f"❌ Установите переменные окружения: {', '.join(missing_vars)}")
        print("Используйте Secrets в Replit для безопасного хранения токенов.")
        return False

    return True

def main():
    """Основная функция"""
    try:
        bot_logger.info("=" * 50)
        bot_logger.info("🚀 Запуск торгового бота v2.0")
        bot_logger.info("=" * 50)

        # Проверяем переменные окружения
        if not validate_environment():
            sys.exit(1)

        # Запускаем Flask сервер
        keep_alive()

        # Настраиваем и запускаем Telegram бота
        bot_logger.info("🔧 Настройка Telegram бота...")
        application = telegram_bot.setup_application()

        bot_logger.info("✅ Бот успешно запущен!")
        bot_logger.info(f"📊 Загружено {watchlist_manager.size()} монет для отслеживания")
        bot_logger.info("🔄 Ожидание команд...")

        # Запускаем polling
        application.run_polling(
            drop_pending_updates=True,
            poll_interval=1.0,
            timeout=20,
            close_loop=False
        )

    except KeyboardInterrupt:
        bot_logger.info("👋 Получен сигнал остановки")
    except Exception as e:
        bot_logger.error(f"💥 Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Graceful shutdown
        try:
            if telegram_bot.bot_running:
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(telegram_bot._stop_current_mode())
                loop.close()
        except Exception as e:
            bot_logger.error(f"Ошибка при остановке: {e}")
        
        bot_logger.info("🛑 Бот остановлен")

if __name__ == "__main__":
    main()