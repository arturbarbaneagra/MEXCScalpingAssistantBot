
#!/usr/bin/env python3
"""
Торговый бот для мониторинга криптовалют на MEXC
Версия: 2.1 - Ультра-быстрая
"""

import os
import sys
import time
import asyncio
import threading
from datetime import datetime
from flask import Flask
from threading import Thread
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Импортируем модули
from logger import bot_logger
from config import config_manager
from watchlist_manager import watchlist_manager
from telegram_bot import telegram_bot
from api_client import api_client
from optimized_api_client import optimized_api_client

# Проверяем переменные окружения
bot_logger.info("Проверка переменных окружения...")
if not os.getenv('TELEGRAM_TOKEN'):
    print("❌ TELEGRAM_TOKEN не найден")
if not os.getenv('TELEGRAM_CHAT_ID'):
    print("❌ TELEGRAM_CHAT_ID не найден")

# Flask приложение
app = Flask(__name__)

@app.route('/')
def health_check():
    """Проверка работоспособности бота"""
    try:
        from metrics_manager import metrics_manager
        from cache_manager import cache_manager
        from alert_manager import alert_manager

        status = {
            'bot_running': telegram_bot.bot_running,
            'bot_mode': telegram_bot.bot_mode,
            'watchlist_size': watchlist_manager.size()
        }

        metrics = metrics_manager.get_summary()
        cache_stats = cache_manager.get_stats()
        alerts = alert_manager.get_active_alerts()

        uptime_hours = metrics.get('uptime_seconds', 0) / 3600

        alert_status = '🟢 OK'
        if alerts:
            critical_alerts = [a for a in alerts if a.get('severity') == 'critical']
            if critical_alerts:
                alert_status = f'🔴 {len(critical_alerts)} Critical'
            else:
                alert_status = f'🟡 {len(alerts)} Warning'

        return f"""
        <html>
        <head>
            <title>🚀 Ultra-Fast Trading Bot v2.1</title>
            <meta http-equiv="refresh" content="15">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; background: #0a0a0a; color: #00ff00; }}
                .container {{ max-width: 900px; margin: 0 auto; background: #1a1a1a; padding: 20px; border-radius: 10px; border: 2px solid #00ff00; }}
                .status-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin: 20px 0; }}
                .metric-box {{ padding: 15px; background: #2a2a2a; border-radius: 8px; border-left: 4px solid #00ff00; }}
                .speed-indicator {{ color: #ff6600; font-weight: bold; }}
                h1 {{ color: #00ff00; text-shadow: 0 0 10px #00ff00; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🚀 Ultra-Fast Trading Bot v2.1</h1>
                <div class="speed-indicator">⚡ MAXIMUM SPEED MODE ACTIVE ⚡</div>

                <div class="status-grid">
                    <div class="metric-box">
                        <strong>Bot Status:</strong> {'🚀 Ultra-Fast Running' if status['bot_running'] else '🔴 Stopped'}<br>
                        <strong>Mode:</strong> {status['bot_mode'] or 'None'}<br>
                        <strong>Uptime:</strong> {uptime_hours:.1f} hours
                    </div>

                    <div class="metric-box">
                        <strong>Watchlist:</strong> {status['watchlist_size']} coins<br>
                        <strong>Active Coins:</strong> {len(telegram_bot.active_coins)}<br>
                        <strong>Cache Entries:</strong> {cache_stats.get('total_entries', 0)}
                    </div>
                </div>

                <div class="metric-box">
                    <strong>🚨 Alerts:</strong> {alert_status}<br>
                    <strong>Speed:</strong> Update every 0.3-0.5 seconds<br>
                    <strong>Memory:</strong> {cache_stats.get('memory_usage_kb', 0):.1f} KB
                </div>

                <div class="status-grid">
                    <div class="metric-box">
                        <strong>API Performance:</strong><br>
                        Total requests: {sum(stats.get('total_requests', 0) for stats in metrics.get('api_stats', {}).values())}<br>
                        Ultra-fast processing enabled
                    </div>

                    <div class="metric-box">
                        <strong>System:</strong><br>
                        Version: 2.1 Ultra-Fast<br>
                        Last update: {time.strftime('%H:%M:%S')}<br>
                        Mode: High Performance
                    </div>
                </div>

                <p style="color: #ff6600;"><small>⚡ Ultra-fast updates every 15 seconds</small></p>
            </div>
        </body>
        </html>
        """
    except Exception as e:
        return f"""
        <html>
        <body style="background: #0a0a0a; color: #00ff00; font-family: Arial;">
            <h1>🚀 Ultra-Fast Trading Bot v2.1</h1>
            <p><strong>Status:</strong> {'🚀 Ultra-Fast Running' if telegram_bot.bot_running else '🔴 Stopped'}</p>
            <p><strong>Mode:</strong> {telegram_bot.bot_mode or 'None'}</p>
            <p><strong>Watchlist:</strong> {watchlist_manager.size()} coins</p>
            <p style="color: #ff6600;"><strong>Error:</strong> {str(e)}</p>
        </body>
        </html>
        """

@app.route('/health')
def health():
    """Health check endpoint"""
    try:
        from health_check import health_checker
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            health_data = loop.run_until_complete(health_checker.full_health_check())
            loop.close()
            return health_data
        except Exception as async_error:
            return {
                'status': 'ultra_fast', 
                'error': f'Async error: {async_error}', 
                'version': '2.1',
                'mode': 'ultra_performance'
            }
    except Exception as e:
        return {'status': 'error', 'error': str(e), 'version': '2.1-ultra'}

def run_flask():
    """Запуск Flask сервера"""
    try:
        app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)
    except Exception as e:
        bot_logger.error(f"Ошибка Flask сервера: {e}")

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
    """Главная функция с исправленным закрытием соединений"""
    try:
        bot_logger.info("=" * 60)
        bot_logger.info("🚀 Запуск УЛЬТРА-БЫСТРОГО торгового бота v2.1")
        bot_logger.info("⚡ MAXIMUM SPEED MODE")
        bot_logger.info("=" * 60)

        # Проверяем переменные окружения
        if not validate_environment():
            sys.exit(1)

        # Инициализируем состояние бота
        from bot_state import bot_state_manager
        bot_state_manager.increment_session()

        # Запускаем автоматическое обслуживание
        from auto_maintenance import auto_maintenance
        maintenance_task = asyncio.create_task(auto_maintenance.start_maintenance_loop())

        # Запускаем Flask сервер
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        bot_logger.info("🌐 Flask сервер запущен на порту 8080")

        # Настраиваем Telegram бота
        app = telegram_bot.setup_application()

        bot_logger.info("🚀 Ультра-быстрый Telegram бот готов")
        bot_logger.info("⚡ Обновления каждые 0.3-0.5 секунд")
        bot_logger.info("🎯 Максимальная производительность активна")
        bot_logger.info("=" * 60)

        start_time = time.time()

        # Запускаем бота
        async with app:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)

            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                bot_logger.info("🛑 Получен сигнал остановки")
            finally:
                # Корректная остановка
                bot_logger.info("🔄 Начинаем корректное закрытие...")
                
                # Останавливаем автоматическое обслуживание
                try:
                    auto_maintenance.stop_maintenance()
                    maintenance_task.cancel()
                    bot_logger.debug("Автоматическое обслуживание остановлено")
                except Exception as e:
                    bot_logger.debug(f"Ошибка остановки обслуживания: {e}")

                # Сохраняем время работы
                uptime = time.time() - start_time
                bot_state_manager.add_uptime(uptime)

                # Останавливаем Telegram бота
                try:
                    await app.updater.stop()
                    await app.stop()
                    bot_logger.debug("Telegram бот остановлен")
                except Exception as e:
                    bot_logger.debug(f"Ошибка остановки Telegram бота: {e}")

    except KeyboardInterrupt:
        bot_logger.info("🛑 Получен сигнал остановки")
    except Exception as e:
        bot_logger.critical(f"💥 Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Корректное закрытие всех соединений
        bot_logger.info("🔒 Закрываем все соединения...")
        
        cleanup_tasks = []
        
        # Закрытие API клиентов
        try:
            cleanup_tasks.append(optimized_api_client.close())
            cleanup_tasks.append(api_client.close())
        except Exception as e:
            bot_logger.debug(f"Ошибка добавления задач закрытия API: {e}")

        # Закрытие WebSocket (если используется)
        try:
            from websocket_client import ws_client
            cleanup_tasks.append(ws_client.close())
        except Exception as e:
            bot_logger.debug(f"WebSocket не найден или уже закрыт: {e}")

        # Выполняем все задачи закрытия
        if cleanup_tasks:
            try:
                await asyncio.gather(*cleanup_tasks, return_exceptions=True)
                bot_logger.debug("Все клиенты закрыты")
            except Exception as e:
                bot_logger.debug(f"Ошибка массового закрытия: {e}")

        # Дополнительная пауза для полного закрытия соединений
        try:
            await asyncio.sleep(0.3)
        except Exception:
            pass

        bot_logger.info("👋 УЛЬТРА-БЫСТРЫЙ торговый бот остановлен")
        bot_logger.info("⚡ Все соединения корректно закрыты")

if __name__ == "__main__":
    # Настройка для предотвращения проблем с event loop в Windows
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
    except Exception as e:
        print(f"\n💥 Критическая ошибка запуска: {e}")
        sys.exit(1)
