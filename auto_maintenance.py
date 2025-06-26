
"""
Модуль автоматического обслуживания системы
"""

import asyncio
import time
import gc
from typing import Dict, Any
from logger import bot_logger
from config import config_manager
from cache_manager import cache_manager
from metrics_manager import metrics_manager
from performance_optimizer import performance_optimizer

class AutoMaintenance:
    """Автоматическое обслуживание системы"""
    
    def __init__(self):
        self.last_cleanup = 0
        self.last_optimization = 0
        self.last_gc = 0
        self.maintenance_interval = 1800  # 30 минут
        self.running = False

    async def start_maintenance_loop(self):
        """Запускает цикл автоматического обслуживания"""
        self.running = True
        bot_logger.info("🔧 Запущено автоматическое обслуживание")
        
        while self.running:
            try:
                await self._perform_maintenance()
                await asyncio.sleep(self.maintenance_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                bot_logger.error(f"Ошибка в цикле обслуживания: {e}")
                await asyncio.sleep(300)  # 5 минут при ошибке

    def stop_maintenance(self):
        """Останавливает обслуживание"""
        self.running = False
        bot_logger.info("🔧 Автоматическое обслуживание остановлено")

    async def _perform_maintenance(self):
        """Выполняет обслуживание"""
        current_time = time.time()
        maintenance_tasks = []

        # Очистка кеша каждые 30 минут
        if current_time - self.last_cleanup > 1800:
            maintenance_tasks.append("cache_cleanup")
            
        # Оптимизация производительности каждые 15 минут
        if current_time - self.last_optimization > 900:
            maintenance_tasks.append("performance_optimization")
            
        # Сборка мусора каждые 10 минут
        if current_time - self.last_gc > 600:
            maintenance_tasks.append("garbage_collection")

        if maintenance_tasks:
            bot_logger.info(f"🔧 Выполняется обслуживание: {', '.join(maintenance_tasks)}")
            
            for task in maintenance_tasks:
                try:
                    if task == "cache_cleanup":
                        await self._cleanup_cache()
                    elif task == "performance_optimization":
                        await self._optimize_performance()
                    elif task == "garbage_collection":
                        await self._garbage_collection()
                except Exception as e:
                    bot_logger.error(f"Ошибка выполнения задачи {task}: {e}")

    async def _cleanup_cache(self):
        """Очищает устаревший кеш"""
        try:
            cache_manager.clear_expired()
            metrics_manager.cleanup_old_metrics()
            self.last_cleanup = time.time()
            bot_logger.info("✅ Кеш и метрики очищены")
        except Exception as e:
            bot_logger.error(f"Ошибка очистки кеша: {e}")

    async def _optimize_performance(self):
        """Оптимизирует производительность"""
        try:
            await performance_optimizer.auto_optimize()
            self.last_optimization = time.time()
            bot_logger.info("✅ Производительность оптимизирована")
        except Exception as e:
            bot_logger.error(f"Ошибка оптимизации: {e}")

    async def _garbage_collection(self):
        """Выполняет сборку мусора"""
        try:
            before = gc.get_count()
            collected = gc.collect()
            after = gc.get_count()
            
            if collected > 0:
                bot_logger.info(f"✅ Сборка мусора: собрано {collected} объектов")
                bot_logger.debug(f"GC счетчики до: {before}, после: {after}")
            
            self.last_gc = time.time()
        except Exception as e:
            bot_logger.error(f"Ошибка сборки мусора: {e}")

    def get_maintenance_stats(self) -> Dict[str, Any]:
        """Возвращает статистику обслуживания"""
        current_time = time.time()
        
        return {
            'maintenance_running': self.running,
            'last_cleanup': self.last_cleanup,
            'last_optimization': self.last_optimization,
            'last_gc': self.last_gc,
            'next_maintenance': current_time + (self.maintenance_interval - (current_time % self.maintenance_interval)),
            'maintenance_interval_minutes': self.maintenance_interval / 60
        }

# Глобальный экземпляр автоматического обслуживания
auto_maintenance = AutoMaintenance()
