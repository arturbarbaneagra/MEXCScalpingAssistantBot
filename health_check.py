import time
import psutil
import asyncio
from typing import Dict, Any
from logger import bot_logger
from config import config_manager
from watchlist_manager import watchlist_manager
from api_client import api_client
from metrics_manager import metrics_manager
from cache_manager import cache_manager
from alert_manager import alert_manager
from circuit_breaker import api_circuit_breakers

class HealthChecker:
    def __init__(self):
        self.last_check_time = 0

    def get_system_info(self) -> Dict[str, Any]:
        """Получает системную информацию"""
        try:
            return {
                'cpu_percent': psutil.cpu_percent(interval=1),
                'memory_percent': psutil.virtual_memory().percent,
                'disk_percent': psutil.disk_usage('/').percent,
                'uptime': time.time() - psutil.boot_time(),
                'python_memory_mb': psutil.Process().memory_info().rss / 1024 / 1024
            }
        except Exception as e:
            bot_logger.error(f"Ошибка получения системной информации: {e}")
            return {
                'cpu_percent': 0,
                'memory_percent': 0,
                'disk_percent': 0,
                'uptime': 0,
                'python_memory_mb': 0,
                'error': str(e)
            }

    def get_bot_status(self) -> Dict[str, Any]:
        """Получает статус бота"""
        try:
            from telegram_bot import telegram_bot
            return {
                'bot_running': telegram_bot.bot_running,
                'bot_mode': telegram_bot.bot_mode,
                'active_coins_count': len(telegram_bot.active_coins),
                'watchlist_size': watchlist_manager.size(),
            }
        except Exception as e:
            bot_logger.error(f"Ошибка получения статуса бота: {e}")
            return {
                'bot_running': False,
                'error': str(e)
            }

    async def check_api_health(self) -> Dict[str, Any]:
        """Проверяет здоровье API"""
        health_status = {
            'api_accessible': False,
            'response_time': 0,
            'circuit_breakers': {}
        }

        try:
            # Проверяем доступность API
            start_time = time.time()
            test_data = await api_client.get_ticker_data("BTC")
            health_status['response_time'] = time.time() - start_time
            health_status['api_accessible'] = test_data is not None

            # Проверяем Circuit Breakers
            for name, cb in api_circuit_breakers.items():
                health_status['circuit_breakers'][name] = {
                    'state': cb.state.value,
                    'failures': cb.failure_count,
                    'last_failure': cb.last_failure_time
                }

        except Exception as e:
            bot_logger.debug(f"Проверка API здоровья: {e}")
            health_status['error'] = str(e)

        return health_status

    async def full_health_check(self) -> Dict[str, Any]:
        """Полная проверка здоровья системы"""
        health_data = {
            'timestamp': time.time(),
            'version': '2.1',
            'status': 'healthy'
        }

        # Системная информация
        system_info = self.get_system_info()
        health_data['system'] = system_info

        # Статус бота
        bot_status = self.get_bot_status()
        health_data['bot'] = bot_status

        # API здоровье
        api_health = await self.check_api_health()
        health_data['api'] = api_health

        # Метрики
        health_data['metrics'] = metrics_manager.get_summary()

        # Кеш
        health_data['cache'] = cache_manager.get_stats()

        # Алерты
        alerts = alert_manager.get_alert_summary()
        health_data['alerts'] = alerts

        # Конфигурация
        health_data['config'] = {
            'volume_threshold': config_manager.get('VOLUME_THRESHOLD'),
            'spread_threshold': config_manager.get('SPREAD_THRESHOLD'),
            'natr_threshold': config_manager.get('NATR_THRESHOLD'),
            'batch_size': config_manager.get('CHECK_BATCH_SIZE')
        }

        # Определяем общий статус
        if (system_info.get('memory_percent', 0) > 90 or 
            not api_health.get('api_accessible', False) or
            len(alerts.get('critical', [])) > 0):
            health_data['status'] = 'unhealthy'
        elif (system_info.get('memory_percent', 0) > 70 or 
              api_health.get('response_time', 0) > 2.0):
            health_data['status'] = 'degraded'

        return health_data

# Глобальный экземпляр
health_checker = HealthChecker()