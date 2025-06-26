
import time
import psutil
import asyncio
from typing import Dict, Any
from logger import bot_logger
from config import config_manager
from watchlist_manager import watchlist_manager
from api_client import api_client

class HealthChecker:
    def __init__(self):
        self.start_time = time.time()

    async def check_api_health(self) -> Dict[str, Any]:
        """Проверка состояния API"""
        try:
            # Проверяем доступность API
            test_data = await api_client.get_current_price_fast('BTC')
            
            return {
                'status': 'healthy' if test_data else 'degraded',
                'response_time': time.time(),
                'api_available': test_data is not None
            }
        except Exception as e:
            return {
                'status': 'unhealthy',
                'error': str(e),
                'api_available': False
            }

    def check_system_health(self) -> Dict[str, Any]:
        """Проверка системных ресурсов"""
        try:
            # Память
            memory = psutil.virtual_memory()
            # CPU
            cpu_percent = psutil.cpu_percent(interval=1)
            # Диск
            disk = psutil.disk_usage('/')
            
            return {
                'memory': {
                    'total': memory.total,
                    'available': memory.available,
                    'percent': memory.percent
                },
                'cpu_percent': cpu_percent,
                'disk': {
                    'total': disk.total,
                    'free': disk.free,
                    'percent': (disk.used / disk.total) * 100
                },
                'uptime': time.time() - self.start_time
            }
        except Exception as e:
            return {
                'error': str(e),
                'status': 'error'
            }

    async def full_health_check(self) -> Dict[str, Any]:
        """Полная проверка здоровья системы"""
        health_data = {
            'timestamp': time.time(),
            'version': '2.0',
            'status': 'healthy'
        }

        # Проверка API
        api_health = await self.check_api_health()
        health_data['api'] = api_health

        # Проверка системы
        system_health = self.check_system_health()
        health_data['system'] = system_health

        # Проверка конфигурации
        health_data['config'] = {
            'watchlist_size': watchlist_manager.size(),
            'volume_threshold': config_manager.get('VOLUME_THRESHOLD'),
            'spread_threshold': config_manager.get('SPREAD_THRESHOLD'),
            'natr_threshold': config_manager.get('NATR_THRESHOLD')
        }

        # Определяем общий статус
        if api_health.get('status') == 'unhealthy':
            health_data['status'] = 'unhealthy'
        elif api_health.get('status') == 'degraded':
            health_data['status'] = 'degraded'

        return health_data

# Глобальный экземпляр
health_checker = HealthChecker()
