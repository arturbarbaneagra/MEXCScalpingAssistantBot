
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

class HealthChecker:
    def __init__(self):
        self.start_time = time.time()

    def get_system_info(self) -> Dict[str, Any]:
        """Получает информацию о системе"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            return {
                'cpu_percent': cpu_percent,
                'memory_total': memory.total,
                'memory_available': memory.available,
                'memory_percent': memory.percent,
                'disk_total': disk.total,
                'disk_used': disk.used,
                'disk_percent': (disk.used / disk.total) * 100,
                'uptime': time.time() - self.start_time
            }
        except Exception as e:
            bot_logger.error(f"Ошибка получения системной информации: {e}")
            return {}

    async def check_api_health(self) -> Dict[str, Any]:
        """Проверяет здоровье API"""
        try:
            start_time = time.time()
            # Проверяем простой запрос к API
            result = await api_client.get_current_price_fast("BTC")
            response_time = time.time() - start_time
            
            if result:
                return {
                    'status': 'healthy',
                    'response_time': response_time,
                    'last_check': time.time()
                }
            else:
                return {
                    'status': 'unhealthy',
                    'response_time': response_time,
                    'last_check': time.time(),
                    'error': 'No response from API'
                }
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e),
                'last_check': time.time()
            }

    def get_bot_status(self) -> Dict[str, Any]:
        """Получает статус бота"""
        from telegram_bot import telegram_bot
        
        return {
            'bot_running': telegram_bot.bot_running,
            'bot_mode': telegram_bot.bot_mode,
            'active_coins_count': len(telegram_bot.active_coins),
            'watchlist_size': watchlist_manager.size(),
            'monitoring_message_id': telegram_bot.monitoring_message_id
        }

    async def full_health_check(self) -> Dict[str, Any]:
        """Выполняет полную проверку здоровья"""
        try:
            system_info = self.get_system_info()
            api_health = await self.check_api_health()
            bot_status = self.get_bot_status()
            
            # Проверяем алерты
            system_alerts = alert_manager.check_system_alerts(system_info)
            api_alerts = alert_manager.check_api_alerts(metrics_manager.get_api_stats())
            all_alerts = system_alerts + api_alerts
            
            # Обрабатываем алерты
            if all_alerts:
                alert_manager.process_alerts(all_alerts)
            
            # Определяем общий статус
            overall_status = 'healthy'
            if api_health.get('status') != 'healthy':
                overall_status = 'degraded'
            if system_info.get('memory_percent', 0) > 90 or system_info.get('cpu_percent', 0) > 90:
                overall_status = 'degraded'
            if any(alert['severity'] == 'critical' for alert in all_alerts):
                overall_status = 'critical'
            
            return {
                'status': overall_status,
                'timestamp': time.time(),
                'system': system_info,
                'api': api_health,
                'bot': bot_status,
                'metrics': metrics_manager.get_summary(),
                'cache': cache_manager.get_stats(),
                'alerts': alert_manager.get_alert_summary(),
                'config': {
                    'volume_threshold': config_manager.get('VOLUME_THRESHOLD'),
                    'spread_threshold': config_manager.get('SPREAD_THRESHOLD'),
                    'natr_threshold': config_manager.get('NATR_THRESHOLD')
                },
                'version': '2.1'
            }
        except Exception as e:
            bot_logger.error(f"Ошибка проверки здоровья: {e}")
            return {
                'status': 'error',
                'error': str(e),
                'timestamp': time.time(),
                'version': '2.0'
            }

# Глобальный экземпляр чекера здоровья
health_checker = HealthChecker()
