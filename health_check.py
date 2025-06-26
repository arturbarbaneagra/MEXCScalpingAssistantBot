
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
    """Комплексная проверка состояния системы"""
    
    def __init__(self):
        self.last_check_time = 0
        self.check_interval = 30  # секунд
        
    def get_system_info(self) -> Dict[str, Any]:
        """Получает информацию о системе"""
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
                'monitoring_message_id': telegram_bot.monitoring_message_id
            }
        except Exception as e:
            bot_logger.error(f"Ошибка получения статуса бота: {e}")
            return {
                'bot_running': False,
                'bot_mode': None,
                'active_coins_count': 0,
                'watchlist_size': 0,
                'error': str(e)
            }

    async def check_api_health(self) -> Dict[str, Any]:
        """Проверяет здоровье API"""
        health_status = {
            'api_accessible': False,
            'response_time': 0,
            'circuit_breakers': {},
            'last_successful_request': 0
        }
        
        try:
            start_time = time.time()
            # Простой запрос для проверки доступности
            result = await api_client._make_request("/ping")
            health_status['response_time'] = time.time() - start_time
            health_status['api_accessible'] = result is not None
            
            # Статус Circuit Breaker'ов
            for name, cb in api_circuit_breakers.items():
                health_status['circuit_breakers'][name] = cb.get_stats()
                
        except Exception as e:
            bot_logger.debug(f"Проверка API здоровья: {e}")
            health_status['error'] = str(e)
        
        return health_status

    async def run_diagnostics(self) -> Dict[str, Any]:
        """Запускает полную диагностику"""
        diagnostics = {
            'timestamp': time.time(),
            'version': '2.1',
            'status': 'healthy'
        }
        
        # Системная информация
        system_info = self.get_system_info()
        diagnostics['system'] = system_info
        
        # Статус бота
        bot_status = self.get_bot_status()
        diagnostics['bot'] = bot_status
        
        # API здоровье
        api_health = await self.check_api_health()
        diagnostics['api'] = api_health
        
        # Метрики
        diagnostics['metrics'] = metrics_manager.get_summary()
        
        # Кеш
        diagnostics['cache'] = cache_manager.get_stats()
        
        # Алерты
        alerts = alert_manager.get_alert_summary()
        diagnostics['alerts'] = alerts
        
        # Конфигурация
        diagnostics['config'] = {
            'volume_threshold': config_manager.get('VOLUME_THRESHOLD'),
            'spread_threshold': config_manager.get('SPREAD_THRESHOLD'),
            'natr_threshold': config_manager.get('NATR_THRESHOLD'),
            'batch_size': config_manager.get('CHECK_BATCH_SIZE')
        }
        
        # Определяем общий статус
        if alerts['active_count'] > 0:
            critical_alerts = [a for a in alerts['active_alerts'] if a.get('severity') == 'critical']
            if critical_alerts:
                diagnostics['status'] = 'critical'
            else:
                diagnostics['status'] = 'warning'
        
        # Проверяем системные ресурсы
        if system_info.get('memory_percent', 0) > 90 or system_info.get('cpu_percent', 0) > 90:
            diagnostics['status'] = 'critical'
        elif system_info.get('memory_percent', 0) > 80 or system_info.get('cpu_percent', 0) > 80:
            if diagnostics['status'] == 'healthy':
                diagnostics['status'] = 'warning'
        
        return diagnostics

    async def full_health_check(self) -> Dict[str, Any]:
        """Полная проверка здоровья системы"""
        current_time = time.time()
        
        # Проверяем интервал
        if current_time - self.last_check_time < self.check_interval:
            return {'status': 'cached', 'message': 'Используются кешированные данные'}
        
        try:
            diagnostics = await self.run_diagnostics()
            
            # Обновляем алерты на основе диагностики
            system_alerts = alert_manager.check_system_alerts(diagnostics['system'])
            api_alerts = alert_manager.check_api_alerts(diagnostics['metrics'].get('api_stats', {}))
            
            all_alerts = system_alerts + api_alerts
            if all_alerts:
                alert_manager.process_alerts(all_alerts)
            
            self.last_check_time = current_time
            return diagnostics
            
        except Exception as e:
            bot_logger.error(f"Ошибка полной проверки здоровья: {e}")
            return {
                'status': 'error',
                'error': str(e),
                'timestamp': current_time,
                'version': '2.1'
            }

# Глобальный экземпляр проверки здоровья
health_checker = HealthChecker()
