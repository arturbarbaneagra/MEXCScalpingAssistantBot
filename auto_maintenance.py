
import time
import asyncio
from logger import bot_logger
from metrics_manager import metrics_manager
from cache_manager import cache_manager
from advanced_alerts import advanced_alert_manager

class AutoMaintenance:
    """Система автоматического обслуживания"""
    
    def __init__(self):
        self.last_cleanup = 0
        self.last_health_report = 0
        self.cleanup_interval = 3600  # 1 час
        self.health_report_interval = 21600  # 6 часов
        
    async def run_maintenance(self):
        """Запуск регулярного обслуживания"""
        current_time = time.time()
        
        # Очистка каждый час
        if current_time - self.last_cleanup > self.cleanup_interval:
            await self._cleanup_system()
            self.last_cleanup = current_time
        
        # Отчет о здоровье каждые 6 часов
        if current_time - self.last_health_report > self.health_report_interval:
            await self._health_report()
            self.last_health_report = current_time
    
    async def _cleanup_system(self):
        """Очистка системы"""
        try:
            # Очистка метрик
            metrics_manager.cleanup_old_metrics()
            
            # Очистка кеша
            cache_manager.cleanup_expired()
            
            # Очистка старых алертов
            if len(advanced_alert_manager.alert_history) > 1000:
                advanced_alert_manager.alert_history = advanced_alert_manager.alert_history[-500:]
            
            bot_logger.info("🧹 Автоматическая очистка системы выполнена")
            
        except Exception as e:
            bot_logger.error(f"Ошибка автоочистки: {e}")
    
    async def _health_report(self):
        """Отчет о здоровье системы"""
        try:
            metrics = metrics_manager.get_summary()
            uptime_hours = metrics.get('uptime_seconds', 0) / 3600
            
            total_requests = sum(
                stats.get('total_requests', 0) 
                for stats in metrics.get('api_stats', {}).values()
            )
            
            cache_stats = cache_manager.get_stats()
            alert_stats = advanced_alert_manager.get_alert_stats()
            
            bot_logger.info(
                f"📊 ОТЧЕТ О ЗДОРОВЬЕ СИСТЕМЫ:\n"
                f"   • Время работы: {uptime_hours:.1f} часов\n"
                f"   • Всего API запросов: {total_requests}\n"
                f"   • Записей в кеше: {cache_stats.get('total_entries', 0)}\n"
                f"   • Всего алертов: {alert_stats.get('total_triggers', 0)}\n"
                f"   • Статус: Система работает стабильно ✅"
            )
            
        except Exception as e:
            bot_logger.error(f"Ошибка отчета о здоровье: {e}")

# Глобальный экземпляр
auto_maintenance = AutoMaintenance()
