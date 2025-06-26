import asyncio
import gc
import time
from logger import bot_logger
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
        bot_logger.info("🛑 Автообслуживание остановлено")

    async def _perform_maintenance(self):
        """Выполняет задачи обслуживания"""
        current_time = time.time()

        tasks = []

        # Очистка кеша каждые 30 минут
        if current_time - self.last_cleanup > 1800:
            tasks.append(self._cleanup_cache())

        # Оптимизация производительности каждые 15 минут
        if current_time - self.last_optimization > 900:
            tasks.append(self._optimize_performance())

        # Сборка мусора каждый час
        if current_time - self.last_gc > 3600:
            tasks.append(self._garbage_collection())

        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
                bot_logger.info("✅ Цикл обслуживания завершен")
            except Exception as e:
                bot_logger.error(f"Ошибка выполнения задачи обслуживания: {e}")

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

    def get_maintenance_stats(self):
        """Возвращает статистику обслуживания"""
        return {
            'running': self.running,
            'last_cleanup': self.last_cleanup,
            'last_optimization': self.last_optimization,
            'last_gc': self.last_gc,
            'maintenance_interval': self.maintenance_interval
        }

# Глобальный экземпляр автообслуживания
auto_maintenance = AutoMaintenance()