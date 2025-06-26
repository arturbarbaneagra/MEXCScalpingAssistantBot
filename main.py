#!/usr/bin/env python3
"""
Торговый бот для мониторинга криптовалют на MEXC
Версия: 2.0
"""

import os
import sys
import asyncio
import threading
from datetime import datetime
from flask import Flask
from threading import Thread
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла в самом начале
load_dotenv()

# Импортируем модули в правильном порядке
from logger import bot_logger
from config import config_manager
from watchlist_manager import watchlist_manager
from telegram_bot import telegram_bot
from api_client import api_client

# Проверяем, что переменные загружены (без вывода значений)
bot_logger.info("Проверка переменных окружения...")
if not os.getenv('TELEGRAM_TOKEN'):
    print("❌ TELEGRAM_TOKEN не найден")
if not os.getenv('TELEGRAM_CHAT_ID'):
    print("❌ TELEGRAM_CHAT_ID не найден")

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
    try:
        from health_check import health_checker
        # Flask не поддерживает async напрямую, используем синхронную версию
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            health_data = loop.run_until_complete(health_checker.full_health_check())
            loop.close()
            return health_data
        except Exception as async_error:
            return {
                'status': 'error', 
                'error': f'Async error: {async_error}', 
                'version': '2.0',
                'system_basic': health_checker.get_system_info(),
                'bot_basic': health_checker.get_bot_status()
            }
    except Exception as e:
        return {'status': 'error', 'error': str(e), 'version': '2.0'}

def run_flask():
    """Запуск Flask сервера"""
    try:
        app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)
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

async def main():
    """Основная функция"""
    try:
        bot_logger.info("=" * 50)
        bot_logger.info("🚀 Запуск торгового бота v2.0")
        bot_logger.info("=" * 50)

        # Проверяем переменные окружения
        if not validate_environment():
            sys.exit(1)

        # Запускаем Flask сервер в отдельном потоке
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        bot_logger.info("🌐 Flask сервер запущен на порту 8080")

        # Настраиваем и запускаем Telegram бота
        app = telegram_bot.setup_application()
        
        bot_logger.info("🤖 Telegram бот готов к работе")
        bot_logger.info("=" * 50)
        
        # Запускаем бота с правильным управлением event loop
        async with app:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            
            # Держим приложение работающим
            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                bot_logger.info("🛑 Получен сигнал остановки")
            finally:
                await app.updater.stop()
                await app.stop()
        
    except KeyboardInterrupt:
        bot_logger.info("🛑 Получен сигнал остановки")
    except Exception as e:
        bot_logger.critical(f"💥 Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Корректное завершение работы
        try:
            await api_client.close()
            # Даем время на полное закрытие соединений
            await asyncio.sleep(0.5)
            bot_logger.info("🔒 API клиент закрыт")
        except Exception as e:
            bot_logger.debug(f"Ошибка закрытия API клиента: {type(e).__name__}")
        
        bot_logger.info("👋 Торговый бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())