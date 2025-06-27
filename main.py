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
telegram_token = os.getenv('TELEGRAM_TOKEN')
telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')

if not telegram_token:
    print("❌ TELEGRAM_TOKEN не найден в переменных окружения")
    bot_logger.error("❌ TELEGRAM_TOKEN отсутствует")
else:
    print(f"✅ TELEGRAM_TOKEN найден (длина: {len(telegram_token)} символов)")
    bot_logger.info(f"✅ TELEGRAM_TOKEN загружен (начинается с: {telegram_token[:10]}...)")

if not telegram_chat_id:
    print("❌ TELEGRAM_CHAT_ID не найден в переменных окружения")
    bot_logger.error("❌ TELEGRAM_CHAT_ID отсутствует")
else:
    print(f"✅ TELEGRAM_CHAT_ID найден: {telegram_chat_id}")
    bot_logger.info(f"✅ TELEGRAM_CHAT_ID загружен: {telegram_chat_id}")

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
                <h1>🤖 MEXCScalping Assistant Status v2.1</h1>

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
            <h1>🤖 MEXCScalping Assistant Status v2.1</h1>
            <p><strong>Status:</strong> {'🟢 Running' if telegram_bot.bot_running else '🔴 Stopped'}</p>
            <p><strong>Mode:</strong> {telegram_bot.bot_mode or 'None'}</p>
            <p><strong>Watchlist:</strong> {watchlist_manager.size()} coins</p>
            <p><strong>Error:</strong> {str(e)}</p>
        </body>
        </html>
        """

@app.route('/api-performance')
def api_performance():
    """Endpoint для мониторинга производительности API"""
    try:
        from api_performance_monitor import api_performance_monitor

        stats = api_performance_monitor.get_all_stats()
        slow_endpoints = api_performance_monitor.get_slow_endpoints()
        error_endpoints = api_performance_monitor.get_error_prone_endpoints()

        # HTML отчет
        html = f"""
        <html>
        <head>
            <title>API Performance Monitor</title>
            <meta http-equiv="refresh" content="30">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
                .container {{ max-width: 1000px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; }}
                .metric {{ padding: 10px; margin: 5px 0; background: #f8f9fa; border-radius: 5px; }}
                .warning {{ background: #fff3cd; border-left: 4px solid #ffc107; }}
                .critical {{ background: #f8d7da; border-left: 4px solid #dc3545; }}
                .healthy {{ background: #d4edda; border-left: 4px solid #28a745; }}
                .endpoint-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🚀 API Performance Monitor</h1>

                <div class="metric">
                    <strong>📊 Общая статистика:</strong><br>
                    Всего запросов: {stats.get('total_requests', 0)}<br>
                    Всего ошибок: {stats.get('total_errors', 0)}<br>
                    Общий процент ошибок: {stats.get('overall_error_rate', 0):.2%}<br>
                    Средний ответ: {stats.get('overall_avg_response_time', 0):.3f}s
                </div>

                {"<div class='metric critical'><strong>🐌 Медленные endpoints:</strong><br>" + "<br>".join(slow_endpoints) + "</div>" if slow_endpoints else ""}

                {"<div class='metric critical'><strong>❌ Проблемные endpoints:</strong><br>" + "<br>".join(error_endpoints) + "</div>" if error_endpoints else ""}

                <h2>📋 Детальная статистика по endpoints:</h2>
                <div class="endpoint-grid">
        """

        for endpoint, endpoint_stats in stats.get('endpoints', {}).items():
            if endpoint_stats.get('status') != 'no_data':
                status_class = endpoint_stats.get('status', 'healthy')
                html += f"""
                    <div class="metric {status_class}">
                        <strong>{endpoint}</strong><br>
                        Запросов: {endpoint_stats.get('total_requests', 0)}<br>
                        Средний ответ: {endpoint_stats.get('avg_response_time', 0):.3f}s<br>
                        Ошибок: {endpoint_stats.get('error_rate', 0):.2%}<br>
                        Статус: {status_class}
                    </div>
                """

        html += """
                </div>
                <p><small>Страница обновляется каждые 30 секунд</small></p>
            </div>
        </body>
        </html>
        """

        return html

    except Exception as e:
        return f"<html><body><h1>API Performance Monitor</h1><p>Ошибка: {e}</p></body></html>"

@app.route('/health')
def health():
    """Health check endpoint"""
    try:
        from health_check import health_checker
        # Безопасная обработка async операций во Flask
        import asyncio

        # Проверяем существующий event loop
        try:
            # Пытаемся получить текущий loop
            current_loop = asyncio.get_running_loop()
            # Если loop уже работает, используем синхронные методы
            return {
                'status': 'running', 
                'version': '2.1',
                'system': health_checker.get_system_info(),
                'bot': health_checker.get_bot_status(),
                'timestamp': time.time()
            }
        except RuntimeError:
            # Нет активного loop, можем создать новый
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    health_data = loop.run_until_complete(health_checker.full_health_check())
                    return health_data
                finally:
                    loop.close()
                    # Очищаем thread-local event loop
                    asyncio.set_event_loop(None)
            except Exception as async_error:
                bot_logger.warning(f"Async health check failed: {async_error}")
                return {
                    'status': 'partial', 
                    'error': f'Async check failed: {str(async_error)[:100]}', 
                    'version': '2.1',
                    'system_basic': health_checker.get_system_info(),
                    'bot_basic': health_checker.get_bot_status(),
                    'timestamp': time.time()
                }
    except Exception as e:
        bot_logger.error(f"Health check error: {e}")
        return {
            'status': 'error', 
            'error': str(e)[:100], 
            'version': '2.1',
            'timestamp': time.time()
        }

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

        # Отправляем приветственное сообщение с диагностикой
        try:
            bot_logger.info(f"🔧 Отправляем приветственное сообщение в chat_id: {telegram_bot.chat_id}")

            # Проверяем, что бот инициализирован
            if not telegram_bot.app or not telegram_bot.app.bot:
                bot_logger.error("❌ Telegram приложение не инициализировано")
                return

            # Тестируем соединение с Telegram API
            bot_info = await telegram_bot.app.bot.get_me()
            bot_logger.info(f"✅ Подключение к Telegram API успешно. Бот: @{bot_info.username}")

            # Отправляем сообщение напрямую через API
            message = await telegram_bot.app.bot.send_message(
                chat_id=telegram_bot.chat_id,
                text=(
                    "👋 <b>Привет! Я тут и жду указаний</b>\n\n"
                    "🤖 Торговый бот v2.1 успешно запущен и готов к работе!\n\n"
                    "💡 <b>Что можно делать:</b>\n"
                    "• 🔔 Запустить режим уведомлений\n"
                    "• 📊 Включить мониторинг списка\n"
                    "• ➕ Добавить новые монеты\n"
                    "• ⚙ Настроить фильтры\n\n"
                    "Выберите действие из меню ниже! 👇"
                ),
                parse_mode="HTML"
            )

            if message:
                bot_logger.info(f"✅ Приветственное сообщение отправлено успешно! Message ID: {message.message_id}")
            else:
                bot_logger.error("❌ Сообщение не было отправлено - получен None")

        except Exception as e:
            bot_logger.error(f"❌ Ошибка отправки приветственного сообщения: {e}")
            bot_logger.error(f"   Тип ошибки: {type(e).__name__}")
            bot_logger.error(f"   Chat ID: {telegram_bot.chat_id}")
            bot_logger.error(f"   Token начинается с: {telegram_bot.token[:10] if telegram_bot.token else 'None'}...")

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
            bot_logger.info("🔄 Начинаем процедуру корректного завершения...")

            # Сначала останавливаем мониторинг
            telegram_bot.bot_running = False

            # Ждем завершения текущих операций
            await asyncio.sleep(1.0)

            # Закрываем API клиент с улучшенной обработкой
            bot_logger.info("🔌 Закрываем API клиент...")
            try:
                await api_client.close()
                # Дополнительная пауза для стабилизации
                await asyncio.sleep(0.3)
                bot_logger.info("✅ API клиент закрыт корректно")
            except Exception as e:
                bot_logger.warning(f"Предупреждение при закрытии API клиента: {e}")
                # Принудительное обнуление сессии
                api_client.session = None

            # Принудительная очистка всех pending tasks
            try:
                current_task = asyncio.current_task()
                pending_tasks = [task for task in asyncio.all_tasks() 
                               if not task.done() and task != current_task]

                if pending_tasks:
                    bot_logger.info(f"🧹 Обнаружено {len(pending_tasks)} pending tasks, отменяем...")

                    # Отменяем все задачи
                    for task in pending_tasks:
                        if not task.cancelled():
                            task.cancel()

                    # Ждем завершения с таймаутом
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*pending_tasks, return_exceptions=True),
                            timeout=3.0
                        )
                        bot_logger.info("✅ Все pending tasks корректно отменены")
                    except asyncio.TimeoutError:
                        bot_logger.warning("⚠️ Таймаут отмены pending tasks")

            except Exception as e:
                bot_logger.debug(f"Ошибка очистки pending tasks: {e}")

            # Сохраняем состояние активных монет
            if hasattr(telegram_bot, 'active_coins') and telegram_bot.active_coins:
                try:
                    import json
                    with open('active_coins_backup.json', 'w') as f:
                        json.dump({
                            k: {
                                'start_time': v.get('start', 0),
                                'last_active': v.get('last_active', 0),
                                'data': v.get('data', {})
                            } for k, v in telegram_bot.active_coins.items()
                        }, f)
                    bot_logger.info("💾 Состояние активных монет сохранено")
                except Exception as e:
                    bot_logger.warning(f"Не удалось сохранить активные монеты: {e}")

            # Очищаем кеши
            try:
                from cache_manager import cache_manager
                cache_manager.clear_all()
                bot_logger.info("🗑️ Кеши очищены")
            except Exception as e:
                bot_logger.debug(f"Ошибка очистки кешей: {e}")

            # Финальное сохранение метрик
            try:
                from metrics_manager import metrics_manager
                import json
                metrics_summary = metrics_manager.get_summary()
                with open('final_metrics.json', 'w') as f:
                    json.dump(metrics_summary, f, indent=2)
                bot_logger.info("📊 Финальные метрики сохранены")
            except Exception as e:
                bot_logger.debug(f"Не удалось сохранить финальные метрики: {e}")

            # Финальная пауза для полного закрытия всех соединений
            await asyncio.sleep(0.5)
            bot_logger.info("🔒 Все компоненты корректно закрыты")

        except Exception as e:
            bot_logger.error(f"Ошибка при завершении работы: {e}")

        bot_logger.info("👋 Торговый бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())