#!/usr/bin/env python3
"""
Торговый бот для мониторинга криптовалют на MEXC
Версия: 2.0
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
    """Проверка работоспособности бота с расширенной информацией"""
    try:
        from metrics_manager import metrics_manager
        from cache_manager import cache_manager
        from alert_manager import alert_manager

        # Получаем базовую информацию
        status = {
            'bot_running': telegram_bot.bot_running,
            'bot_mode': telegram_bot.bot_mode,
            'watchlist_size': watchlist_manager.size()
        }

        # Получаем метрики
        metrics = metrics_manager.get_summary()
        cache_stats = cache_manager.get_stats()
        alerts = alert_manager.get_active_alerts()

        # Получаем алерты из единой системы
        advanced_alerts = alert_manager.get_active_alerts()
        alert_stats = alert_manager.get_alert_stats()

        # Получаем оценку производительности
        try:
            from performance_optimizer import performance_optimizer
            performance_score = performance_optimizer.get_performance_score()
        except:
            performance_score = 100.0

        # Время работы
        uptime_hours = metrics.get('uptime_seconds', 0) / 3600

        # Статус алертов
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
            <title>Trading Bot Status v2.1</title>
            <meta http-equiv="refresh" content="30">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
                .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; }}
                .status-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin: 20px 0; }}
                .metric-box {{ padding: 15px; background: #f8f9fa; border-radius: 8px; border-left: 4px solid #007bff; }}
                .alert-box {{ padding: 10px; background: #fff3cd; border-radius: 5px; margin: 5px 0; }}
                .critical {{ border-left-color: #dc3545; background: #f8d7da; }}
                .warning {{ border-left-color: #ffc107; background: #fff3cd; }}
                .success {{ border-left-color: #28a745; background: #d4edda; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🤖 Trading Bot Status v2.1</h1>

                <div class="status-grid">
                    <div class="metric-box {'success' if status['bot_running'] else 'critical'}">
                        <strong>Bot Status:</strong> {'🟢 Running' if status['bot_running'] else '🔴 Stopped'}<br>
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
                    {f"Recent alerts: {', '.join([a.get('message', '')[:50] + '...' if len(a.get('message', '')) > 50 else a.get('message', '') for a in alerts[:2]])}" if alerts else "No active alerts"}<br>
                    <strong>Advanced:</strong> {len(advanced_alerts)} active, {alert_stats.get('total_triggers', 0)} total triggers
                </div>

                <div class="status-grid">
                    <div class="metric-box">
                        <strong>API Performance:</strong><br>
                        Total requests: {sum(stats.get('total_requests', 0) for stats in metrics.get('api_stats', {}).values())}<br>
                        Performance score: {performance_score:.0f}/100<br>
                        Memory usage: {cache_stats.get('memory_usage_kb', 0):.1f} KB
                    </div>

                    <div class="metric-box">
                        <strong>System:</strong><br>
                        Version: 2.1<br>
                        Last update: {time.strftime('%H:%M:%S')}
                    </div>
                </div>

                <p><small>Page auto-refreshes every 30 seconds</small></p>
            </div>
        </body>
        </html>
        """
    except Exception as e:
        return f"""
        <html>
        <body>
            <h1>🤖 Trading Bot Status v2.1</h1>
            <p><strong>Status:</strong> {'🟢 Running' if telegram_bot.bot_running else '🔴 Stopped'}</p>
            <p><strong>Mode:</strong> {telegram_bot.bot_mode or 'None'}</p>
            <p><strong>Watchlist:</strong> {watchlist_manager.size()} coins</p>
            <p><strong>Error:</strong> {str(e)}</p>
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
        bot_logger.info("🚀 Запуск торгового бота v2.1")
        bot_logger.info("=" * 50)

        # Проверяем переменные окружения
        if not validate_environment():
            sys.exit(1)

        # Инициализируем состояние бота
        from bot_state import bot_state_manager
        bot_state_manager.increment_session()

        # Запускаем автоматическое обслуживание
        from auto_maintenance import auto_maintenance
        maintenance_task = asyncio.create_task(auto_maintenance.start_maintenance_loop())

        # Запускаем Flask сервер в отдельном потоке
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        bot_logger.info("🌐 Flask сервер запущен на порту 8080")

        # Настраиваем и запускаем Telegram бота
        app = telegram_bot.setup_application()

        bot_logger.info("🤖 Telegram бот готов к работе")
        bot_logger.info("🔧 Автоматическое обслуживание активно")
        bot_logger.info("=" * 50)

        start_time = time.time()

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
                # Останавливаем автоматическое обслуживание
                auto_maintenance.stop_maintenance()
                maintenance_task.cancel()

                # Сохраняем время работы
                uptime = time.time() - start_time
                bot_state_manager.add_uptime(uptime)

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
            # Сначала останавливаем мониторинг
            telegram_bot.bot_running = False
            
            # Закрываем API клиент
            await api_client.close()
            
            # Сохраняем состояние активных монет
            if hasattr(telegram_bot, 'active_coins') and telegram_bot.active_coins:
                try:
                    with open('active_coins_backup.json', 'w') as f:
                        json.dump({
                            k: {
                                'start_time': v.get('start_time', 0),
                                'last_active': v.get('last_active', 0),
                                'initial_data': v.get('initial_data', {})
                            } for k, v in telegram_bot.active_coins.items()
                        }, f)
                    bot_logger.info("💾 Состояние активных монет сохранено")
                except Exception as e:
                    bot_logger.warning(f"Не удалось сохранить активные монеты: {e}")
            
            # Очищаем кеши
            cache_manager.clear_all()
            
            # Финальное сохранение метрик
            try:
                metrics_summary = metrics_manager.get_summary()
                with open('final_metrics.json', 'w') as f:
                    json.dump(metrics_summary, f, indent=2)
            except Exception as e:
                bot_logger.debug(f"Не удалось сохранить финальные метрики: {e}")
            
            # Даем время на полное закрытие соединений
            await asyncio.sleep(1.0)
            bot_logger.info("🔒 Все компоненты корректно закрыты")
        except Exception as e:
            bot_logger.error(f"Ошибка при завершении работы: {e}")

        bot_logger.info("👋 Торговый бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())