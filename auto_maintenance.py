import asyncio
import gc
import time
from logger import bot_logger
from cache_manager import cache_manager
from metrics_manager import metrics_manager
from performance_optimizer import performance_optimizer
from circuit_breaker import api_circuit_breakers

class AutoMaintenance:
    def __init__(self):
        self.running = False
        self.maintenance_task = None
        self.last_cleanup = 0
        self.cleanup_interval = 3600  # 1 час

    async def start_maintenance_loop(self):
        """Запуск цикла автоматического обслуживания"""
        self.running = True
        bot_logger.info("🔧 Запуск автоматического обслуживания")

        while self.running:
            try:
                current_time = time.time()

                # Выполняем обслуживание каждый час
                if current_time - self.last_cleanup > self.cleanup_interval:
                    await self._perform_maintenance()
                    self.last_cleanup = current_time

                # Лёгкое обслуживание каждые 10 минут
                await self._light_maintenance()

                await asyncio.sleep(600)  # 10 минут

            except asyncio.CancelledError:
                break
            except Exception as e:
                bot_logger.error(f"Ошибка в цикле обслуживания: {e}")
                await asyncio.sleep(300)  # 5 минут при ошибке

    def stop_maintenance(self):
        """Остановка обслуживания"""
        self.running = False
        if self.maintenance_task and not self.maintenance_task.done():
            self.maintenance_task.cancel()

    async def _perform_maintenance(self):
        """Выполняет полное обслуживание"""
        bot_logger.info("🔧 Начало полного обслуживания системы")

        try:
            await self._cleanup_cache()
            await self._optimize_performance()
            await self._garbage_collection()
            await self._cleanup_logs()
            await self._validate_system()

            bot_logger.info("✅ Полное обслуживание завершено")
        except Exception as e:
            bot_logger.error(f"Ошибка полного обслуживания: {e}")

    async def _light_maintenance(self):
        """Лёгкое обслуживание"""
        try:
            # Очистка устаревших кешей
            cache_manager.clear_expired()

            # Сборка мусора если нужно
            if gc.get_count()[0] > 1000:
                gc.collect()

        except Exception as e:
            bot_logger.debug(f"Ошибка лёгкого обслуживания: {e}")

    async def _cleanup_cache(self):
        """Очистка кешей"""
        try:
            cache_manager.clear_expired()
            bot_logger.debug("✅ Очистка кешей завершена")
        except Exception as e:
            bot_logger.error(f"Ошибка очистки кешей: {e}")

    async def _optimize_performance(self):
        """Оптимизация производительности"""
        try:
            await performance_optimizer.optimize()
            bot_logger.debug("✅ Оптимизация производительности завершена")
        except Exception as e:
            bot_logger.error(f"Ошибка оптимизации: {e}")

    async def _garbage_collection(self):
        """Сборка мусора"""
        try:
            collected = gc.collect()
            bot_logger.debug(f"✅ Собрано {collected} объектов мусора")
        except Exception as e:
            bot_logger.error(f"Ошибка сборки мусора: {e}")

    async def force_maintenance(self):
        """Принудительное обслуживание системы"""
        bot_logger.info("🔧 Принудительное обслуживание системы")

        try:
            await self._cleanup_cache()
            await self._optimize_performance()
            await self._garbage_collection()
            await self._cleanup_logs()
            await self._validate_system()

            bot_logger.info("✅ Принудительное обслуживание завершено")
        except Exception as e:
            bot_logger.error(f"Ошибка принудительного обслуживания: {e}")

    async def _cleanup_logs(self):
        """Очистка и ротация логов"""
        try:
            from log_rotator import log_rotator

            # Ротация основного лог файла
            main_log = "trading_bot.log"
            if log_rotator.should_rotate(main_log):
                log_rotator.rotate_log(main_log)

            # Очистка старых логов (старше 30 дней)
            log_rotator.cleanup_by_age(max_days=30)

            bot_logger.debug("✅ Очистка логов завершена")

        except Exception as e:
            bot_logger.error(f"Ошибка очистки логов: {e}")

    async def _validate_system(self):
        """Валидация системы"""
        try:
            # Проверяем состояние Circuit Breakers
            for name, cb in api_circuit_breakers.items():
                if cb.state.value == 'open':
                    bot_logger.warning(f"Circuit Breaker {name} открыт")

            bot_logger.debug("✅ Валидация системы завершена")

        except Exception as e:
            bot_logger.error(f"Ошибка валидации системы: {e}")

# Глобальный экземпляр
auto_maintenance = AutoMaintenance()