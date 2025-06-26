
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
        self.last_check = 0
        self.check_interval = 300  # 5 минут
        
    def get_system_health(self) -> Dict[str, Any]:
        """Получает информацию о состоянии системы"""
        try:
            memory = psutil.virtual_memory()
            cpu_percent = psutil.cpu_percent(interval=1)
            
            return {
                'memory_usage': f"{memory.percent}%",
                'memory_available': f"{memory.available / 1024 / 1024:.1f} MB",
                'cpu_usage': f"{cpu_percent}%",
                'uptime': time.time() - psutil.boot_time()
            }
        except Exception as e:
            bot_logger.error(f"Ошибка получения системной информации: {e}")
            return {'error': str(e)}
    
    async def check_api_health(self) -> Dict[str, Any]:
        """Проверяет доступность API"""
        try:
            start_time = time.time()
            # Тестовый запрос к API
            test_data = await asyncio.to_thread(api_client.get_current_price, "BTCUSDT")
            response_time = time.time() - start_time
            
            return {
                'api_available': test_data is not None,
                'response_time': f"{response_time:.3f}s",
                'test_price': test_data if test_data else 'N/A'
            }
        except Exception as e:
            return {
                'api_available': False,
                'error': str(e)
            }
    
    def get_bot_metrics(self) -> Dict[str, Any]:
        """Получает метрики бота"""
        return {
            'watchlist_size': watchlist_manager.size(),
            'config_loaded': len(config_manager.config) > 0,
            'log_file_exists': True  # Упрощенная проверка
        }
    
    async def full_health_check(self) -> Dict[str, Any]:
        """Полная проверка здоровья системы"""
        if time.time() - self.last_check < self.check_interval:
            return {'status': 'cached', 'message': 'Используется кэшированный результат'}
        
        self.last_check = time.time()
        
        system_health = self.get_system_health()
        api_health = await self.check_api_health()
        bot_metrics = self.get_bot_metrics()
        
        # Определяем общий статус
        overall_status = 'healthy'
        if not api_health.get('api_available', False):
            overall_status = 'degraded'
        if system_health.get('error') or float(system_health.get('memory_usage', '0%').replace('%', '')) > 90:
            overall_status = 'unhealthy'
        
        return {
            'timestamp': time.time(),
            'overall_status': overall_status,
            'system': system_health,
            'api': api_health,
            'bot': bot_metrics
        }

# Глобальный экземпляр
health_checker = HealthChecker()
