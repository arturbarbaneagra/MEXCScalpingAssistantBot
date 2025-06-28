#!/usr/bin/env python3
"""
MEXCScalping Assistant для мониторинга криптовалют на MEXC
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
from session_recorder import session_recorder

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

        # Получаем статистику записи сессий
        session_stats = session_recorder.get_stats()

        # Получаем статистику автономного мониторинга
        try:
            from autonomous_activity_monitor import autonomous_monitor
            monitor_stats = autonomous_monitor.get_stats()
        except:
            monitor_stats = {'running': False, 'active_activities': 0}

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

        # Получаем статистику состояния бота
        from bot_state import bot_state_manager
        bot_statistics = bot_state_manager.get_statistics()
        health_indicators = bot_state_manager.get_health_indicators()

        # Получаем статистику оптимизатора
        optimization_stats = performance_optimizer.get_optimization_stats()

        return f"""
        <html>
        <head>
            <title>MEXCScalping Assistant Status v2.1 Pro</title>
            <meta http-equiv="refresh" content="30">
            <style>
                body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 0; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; }}
                .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
                .header {{ text-align: center; color: white; margin-bottom: 30px; }}
                .header h1 {{ font-size: 2.5em; margin: 0; text-shadow: 2px 2px 4px rgba(0,0,0,0.3); }}
                .header p {{ font-size: 1.2em; opacity: 0.9; }}
                .status-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin: 20px 0; }}
                .metric-box {{ padding: 20px; background: white; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); border-left: 5px solid #007bff; }}
                .metric-box h3 {{ margin: 0 0 15px 0; color: #333; font-size: 1.3em; }}
                .critical {{ border-left-color: #dc3545; }}
                .warning {{ border-left-color: #ffc107; }}
                .success {{ border-left-color: #28a745; }}
                .excellent {{ border-left-color: #00c851; }}
                .metric-item {{ margin: 8px 0; padding: 5px 0; border-bottom: 1px solid #eee; }}
                .metric-item:last-child {{ border-bottom: none; }}
                .metric-value {{ font-weight: bold; color: #007bff; }}
                .health-score {{ font-size: 2em; font-weight: bold; text-align: center; padding: 10px; border-radius: 8px; }}
                .health-excellent {{ background: #d4edda; color: #155724; }}
                .health-good {{ background: #d1ecf1; color: #0c5460; }}
                .health-fair {{ background: #fff3cd; color: #856404; }}
                .health-poor {{ background: #f8d7da; color: #721c24; }}
                .progress-bar {{ width: 100%; height: 20px; background: #eee; border-radius: 10px; overflow: hidden; }}
                .progress-fill {{ height: 100%; background: linear-gradient(90deg, #28a745, #20c997); transition: width 0.3s ease; }}
                .footer {{ text-align: center; color: white; margin-top: 30px; opacity: 0.8; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🤖 MEXCScalping Assistant</h1>
                    <p>Professional Trading Bot v2.1 Pro • Enhanced Performance Dashboard</p>
                </div>

                <div class="status-grid">
                    <div class="metric-box {'success' if status['bot_running'] else 'critical'}">
                        <h3>🤖 Bot Status</h3>
                        <div class="metric-item">Status: <span class="metric-value">{'🟢 Running' if status['bot_running'] else '🔴 Stopped'}</span></div>
                        <div class="metric-item">Mode: <span class="metric-value">{status['bot_mode'] or 'None'}</span></div>
                        <div class="metric-item">Uptime: <span class="metric-value">{uptime_hours:.1f} hours</span></div>
                        <div class="metric-item">Session: <span class="metric-value">#{bot_statistics['session_count']}</span></div>
                    </div>

                    <div class="metric-box excellent">
                        <h3>📊 System Health</h3>
                        <div class="health-score health-{health_indicators['status']}">{health_indicators['health_score']}/100</div>
                        <div class="metric-item">Status: <span class="metric-value">{health_indicators['status'].title()}</span></div>
                        <div class="metric-item">Stability: <span class="metric-value">{health_indicators['uptime_stability']:.0f}%</span></div>
                        {f"<div class='metric-item'>Issues: <span class='metric-value'>{', '.join(health_indicators['issues'][:2])}</span></div>" if health_indicators['issues'] else ""}
                    </div>

                    <div class="metric-box">
                        <h3>💰 Trading Data</h3>
                        <div class="metric-item">Watchlist: <span class="metric-value">{status['watchlist_size']} coins</span></div>
                        <div class="metric-item">Active Coins: <span class="metric-value">{len(telegram_bot.active_coins)}</span></div>
                        <div class="metric-item">Total Monitored: <span class="metric-value">{bot_statistics['total_coins_monitored']:,}</span></div>
                        <div class="metric-item">Alerts Sent: <span class="metric-value">{bot_statistics['total_alerts_sent']:,}</span></div>
                    </div>

                    <div class="metric-box {'warning' if performance_score < 70 else 'success'}">
                        <h3>⚡ Performance</h3>
                        <div class="metric-item">Score: <span class="metric-value">{performance_score:.0f}/100</span></div>
                        <div class="progress-bar"><div class="progress-fill" style="width: {performance_score}%"></div></div>
                        <div class="metric-item">API Requests: <span class="metric-value">{sum(stats.get('total_requests', 0) for stats in metrics.get('api_stats', {}).values()):,}</span></div>
                        <div class="metric-item">Optimizations: <span class="metric-value">{optimization_stats['successful_optimizations']}/{optimization_stats['total_optimizations']}</span></div>
                    </div>

                    <div class="metric-box {'critical' if len(alerts) > 0 else 'success'}">
                        <h3>🚨 Alerts & Monitoring</h3>
                        <div class="metric-item">Status: <span class="metric-value">{alert_status}</span></div>
                        <div class="metric-item">Active Alerts: <span class="metric-value">{len(advanced_alerts)}</span></div>
                        <div class="metric-item">Total Triggers: <span class="metric-value">{alert_stats.get('total_triggers', 0)}</span></div>
                        <div class="metric-item">Recent Errors: <span class="metric-value">{health_indicators['error_rate']}</span></div>
                    </div>

                    <div class="metric-box">
                        <h3>💾 Cache & Memory</h3>
                        <div class="metric-item">Cache Entries: <span class="metric-value">{cache_stats.get('total_entries', 0)}</span></div>
                        <div class="metric-item">Memory Usage: <span class="metric-value">{cache_stats.get('memory_usage_kb', 0):.1f} KB</span></div>
                        <div class="metric-item">Hit Rate: <span class="metric-value">{cache_stats.get('hit_rate', 0):.1f}%</span></div>
                        <div class="metric-item">TTL: <span class="metric-value">{config_manager.get('CACHE_TTL_SECONDS')}s</span></div>
                    </div>

                    <div class="metric-box">
                        <h3>📝 Data Recording</h3>
                        <div class="metric-item">Session Recorder: <span class="metric-value">{'🟢 Active' if session_stats['recording'] else '🔴 Stopped'}</span></div>
                        <div class="metric-item">Active Sessions: <span class="metric-value">{session_stats['active_sessions']}</span></div>
                        <div class="metric-item">Data Directory: <span class="metric-value">{session_stats['data_directory']}</span></div>
                    </div>

                    <div class="metric-box">
                        <h3>🔍 Autonomous Monitor</h3>
                        <div class="metric-item">Status: <span class="metric-value">{'🟢 Active' if monitor_stats['running'] else '🔴 Stopped'}</span></div>
                        <div class="metric-item">Activities: <span class="metric-value">{monitor_stats['active_activities']}</span></div>
                        <div class="metric-item">Success Rate: <span class="metric-value">{bot_statistics['success_rate_percent']:.1f}%</span></div>
                    </div>
                </div>

                <div class="footer">
                    <p>🕒 Last update: {time.strftime('%Y-%m-%d %H:%M:%S')} • Auto-refresh: 30s</p>
                    <p>💡 Total uptime: {bot_statistics['total_uptime_hours']:.1f} hours across {bot_statistics['startup_count']} sessions</p>
                </div>
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

@app.route('/sessions')
def sessions_view():
    """Endpoint для просмотра записанных сессий"""
    try:
        from datetime import datetime, timedelta

        # Получаем статистику
        stats = session_recorder.get_stats()

        # Получаем данные за последние 7 дней
        sessions_data = {}
        for i in range(7):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            daily_data = session_recorder.get_daily_summary(date)
            if daily_data:
                sessions_data[date] = daily_data

        html = f"""
        <html>
        <head>
            <title>Session Recorder</title>
            <meta http-equiv="refresh" content="60">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
                .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; }}
                .metric {{ padding: 10px; margin: 5px 0; background: #f8f9fa; border-radius: 5px; }}
                .session {{ padding: 8px; margin: 3px 0; background: #e9ecef; border-radius: 3px; }}
                .active {{ background: #d4edda; border-left: 4px solid #28a745; }}
                .date-section {{ margin: 15px 0; padding: 10px; background: #fff3cd; border-radius: 5px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📝 Session Recorder</h1>

                <div class="metric {'active' if stats['recording'] else ''}">
                    <strong>Статус:</strong> {'🟢 Запись активна' if stats['recording'] else '🔴 Запись остановлена'}<br>
                    <strong>Активных сессий:</strong> {stats['active_sessions']}<br>
                    <strong>Директория данных:</strong> {stats['data_directory']}<br>
                    {"<strong>Текущие символы:</strong> " + ", ".join(stats['session_symbols']) if stats['session_symbols'] else ""}
                </div>

                <h2>📊 Последние 7 дней</h2>
        """

        if sessions_data:
            for date, daily_data in sorted(sessions_data.items(), reverse=True):
                metadata = daily_data.get('metadata', {})
                sessions = daily_data.get('sessions', [])

                html += f"""
                <div class="date-section">
                    <h3>{date}</h3>
                    <p>Всего сессий: {metadata.get('total_sessions', 0)}, 
                       Общая длительность: {metadata.get('total_duration', 0)/60:.1f} минут</p>

                    <div style="max-height: 300px; overflow-y: auto;">
                """

                for session in sessions[-10:]:  # Показываем последние 10 сессий
                    duration_min = session.get('total_duration', 0) / 60
                    summary = session.get('summary', {})

                    html += f"""
                    <div class="session">
                        <strong>{session['symbol']}</strong> - {duration_min:.1f} мин 
                        ({session.get('total_minutes', 0)} интервалов)<br>
                        Сделок: {summary.get('total_trades', 0)}, 
                        Оборот: ${summary.get('total_volume', 0):,.0f}<br>
                        <small>{session.get('start_datetime', '')[:19]} - {session.get('end_datetime', '')[:19]}</small>
                    </div>
                    """

                html += "</div></div>"
        else:
            html += "<p>Нет данных о сессиях за последние 7 дней</p>"

        html += """
                <p><small>Страница обновляется каждую минуту</small></p>
            </div>
        </body>
        </html>
        """

        return html

    except Exception as e:
        return f"<html><body><h1>Session Recorder</h1><p>Ошибка: {e}</p></body></html>"

@app.route('/performance')
def performance_dashboard():
    """Dashboard управления производительностью"""
    try:
        from performance_optimizer import performance_optimizer
        from config_validator import config_validator
        
        # Получаем статистику оптимизатора
        opt_stats = performance_optimizer.get_optimization_stats()
        
        # Получаем текущую конфигурацию и рекомендации
        current_config = config_manager.get_all()
        is_valid, errors = config_validator.validate_config(current_config)
        recommendations = config_validator.get_recommendations(current_config)
        
        html = f"""
        <html>
        <head>
            <title>Performance Management Dashboard</title>
            <meta http-equiv="refresh" content="60">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
                .container {{ max-width: 1000px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; }}
                .metric {{ padding: 15px; margin: 10px 0; background: #f8f9fa; border-radius: 8px; border-left: 4px solid #007bff; }}
                .warning {{ border-left-color: #ffc107; background: #fff3cd; }}
                .critical {{ border-left-color: #dc3545; background: #f8d7da; }}
                .success {{ border-left-color: #28a745; background: #d4edda; }}
                .config-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }}
                .recommendation {{ padding: 10px; margin: 5px 0; background: #e3f2fd; border-radius: 5px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>⚡ Performance Management Dashboard</h1>
                
                <div class="metric {'success' if opt_stats['optimization_enabled'] else 'warning'}">
                    <h3>🔧 Optimization Status</h3>
                    <p><strong>Auto-optimization:</strong> {'✅ Enabled' if opt_stats['optimization_enabled'] else '⏸️ Disabled'}</p>
                    <p><strong>Total optimizations:</strong> {opt_stats['total_optimizations']}</p>
                    <p><strong>Successful:</strong> {opt_stats['successful_optimizations']}</p>
                    <p><strong>Current score:</strong> {opt_stats['current_score']:.0f}/100</p>
                </div>

                <div class="config-grid">
                    <div class="metric {'critical' if not is_valid else 'success'}">
                        <h3>⚙️ Configuration Status</h3>
                        <p><strong>Validation:</strong> {'❌ Errors found' if not is_valid else '✅ Valid'}</p>
                        {f"<p><strong>Errors:</strong><br>{'<br>'.join(errors[:3])}</p>" if errors else ""}
                        <p><strong>Batch Size:</strong> {current_config.get('CHECK_BATCH_SIZE', 'N/A')}</p>
                        <p><strong>Batch Interval:</strong> {current_config.get('CHECK_BATCH_INTERVAL', 'N/A')}s</p>
                        <p><strong>API Timeout:</strong> {current_config.get('API_TIMEOUT', 'N/A')}s</p>
                    </div>

                    <div class="metric">
                        <h3>🎯 Trading Thresholds</h3>
                        <p><strong>Volume:</strong> ${current_config.get('VOLUME_THRESHOLD', 'N/A'):,}</p>
                        <p><strong>Spread:</strong> {current_config.get('SPREAD_THRESHOLD', 'N/A')}%</p>
                        <p><strong>NATR:</strong> {current_config.get('NATR_THRESHOLD', 'N/A')}%</p>
                        <p><strong>Cache TTL:</strong> {current_config.get('CACHE_TTL_SECONDS', 'N/A')}s</p>
                    </div>
                </div>

                {"<div class='metric warning'><h3>💡 Recommendations</h3>" + "<br>".join([f"• {rec}" for rec in recommendations[:5]]) + "</div>" if recommendations else ""}

                <div class="metric">
                    <h3>📊 Recent Performance History</h3>
                    <div style="max-height: 200px; overflow-y: auto;">
        """
        
        for i, record in enumerate(opt_stats['recent_history'][-5:]):
            actions = record.get('actions_taken', [])
            timestamp = time.strftime('%H:%M:%S', time.localtime(record['timestamp']))
            html += f"""
                        <div class="recommendation">
                            <strong>{timestamp}:</strong> Score: {record['metrics'].get('performance_score', 'N/A')}/100<br>
                            {f"Actions: {', '.join(actions)}" if actions else "No actions taken"}
                        </div>
            """
        
        html += f"""
                    </div>
                </div>

                <div class="metric">
                    <h3>🔄 Auto-optimization Improvements</h3>
                    <div style="max-height: 150px; overflow-y: auto;">
        """
        
        for improvement in opt_stats.get('performance_improvements', [])[-3:]:
            timestamp = time.strftime('%H:%M:%S', time.localtime(improvement['timestamp']))
            html += f"""
                        <div class="recommendation">
                            <strong>{timestamp}:</strong> {', '.join(improvement['actions'])}<br>
                            Performance before: {improvement.get('performance_before', 'N/A'):.3f}s
                        </div>
            """
        
        html += """
                    </div>
                </div>

                <p><small>Dashboard auto-refreshes every 60 seconds</small></p>
            </div>
        </body>
        </html>
        """
        
        return html

    except Exception as e:
        return f"<html><body><h1>Performance Dashboard</h1><p>Error: {e}</p></body></html>"

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
        bot_logger.info("🚀 Запуск MEXCScalping Assistant v2.1")
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

        # Запускаем запись сессий
        session_recorder.start_recording()

        # Запускаем автономный монитор активности
        from autonomous_activity_monitor import autonomous_monitor
        await autonomous_monitor.start()

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
                    "🤖 MEXCScalping Assistant v2.1 успешно запущен и готов к работе!\n\n"
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

                # Останавливаем автономный монитор
                await autonomous_monitor.stop()

                # Останавливаем запись сессий
                session_recorder.stop_recording()

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

        bot_logger.info("👋 MEXCScalping Assistant остановлен")

if __name__ == "__main__":
    asyncio.run(main())