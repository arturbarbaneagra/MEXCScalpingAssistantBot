
#!/usr/bin/env python3
"""
Система автоматического обслуживания торгового бота
"""

import asyncio
import time
import os
import gc
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from logger import bot_logger


class AutoMaintenance:
    """Система автоматического обслуживания"""
    
    def __init__(self):
        self.running = False
        self.maintenance_task: Optional[asyncio.Task] = None
        self.maintenance_interval = 3600  # 1 час
        self.last_maintenance = time.time()
        self.maintenance_stats = {
            'total_runs': 0,
            'last_run': None,
            'cache_clears': 0,
            'log_rotations': 0,
            'memory_cleanups': 0
        }
    
    async def start_maintenance_loop(self):
        """Запуск цикла автоматического обслуживания"""
        if self.running:
            bot_logger.warning("Автоматическое обслуживание уже запущено")
            return
        
        self.running = True
        bot_logger.info("🔧 Запуск автоматического обслуживания")
        
        while self.running:
            try:
                current_time = time.time()
                
                # Проверяем, нужно ли выполнить обслуживание
                if current_time - self.last_maintenance >= self.maintenance_interval:
                    await self._perform_maintenance()
                    self.last_maintenance = current_time
                
                # Ждем 5 минут до следующей проверки
                await asyncio.sleep(300)
                
            except asyncio.CancelledError:
                bot_logger.info("🛑 Автоматическое обслуживание отменено")
                break
            except Exception as e:
                bot_logger.error(f"Ошибка в цикле автоматического обслуживания: {e}")
                await asyncio.sleep(60)  # Ждем минуту при ошибке
    
    async def _perform_maintenance(self):
        """Выполнение процедур обслуживания"""
        bot_logger.info("🔧 Начинаем автоматическое обслуживание...")
        
        try:
            # 1. Очистка кешей
            await self._cleanup_caches()
            
            # 2. Ротация логов
            await self._rotate_logs()
            
            # 3. Очистка памяти
            await self._cleanup_memory()
            
            # 4. Проверка системных ресурсов
            await self._check_system_resources()
            
            # Обновляем статистику
            self.maintenance_stats['total_runs'] += 1
            self.maintenance_stats['last_run'] = datetime.now().isoformat()
            
            bot_logger.info("✅ Автоматическое обслуживание завершено")
            
        except Exception as e:
            bot_logger.error(f"Ошибка при выполнении обслуживания: {e}")
    
    async def _cleanup_caches(self):
        """Очистка устаревших кешей"""
        try:
            from cache_manager import cache_manager
            
            # Очищаем устаревшие записи
            cleaned_count = cache_manager.cleanup_expired()
            
            if cleaned_count > 0:
                bot_logger.info(f"🗑️ Очищено {cleaned_count} устаревших записей кеша")
                self.maintenance_stats['cache_clears'] += 1
            
        except Exception as e:
            bot_logger.warning(f"Ошибка очистки кешей: {e}")
    
    async def _rotate_logs(self):
        """Ротация файлов логов"""
        try:
            from log_rotator import log_rotator
            
            # Проверяем размер основного лога
            log_file = 'trading_bot.log'
            if os.path.exists(log_file):
                file_size = os.path.getsize(log_file)
                # Если файл больше 10MB, делаем ротацию
                if file_size > 10 * 1024 * 1024:
                    log_rotator.rotate_logs()
                    bot_logger.info("📋 Выполнена ротация логов")
                    self.maintenance_stats['log_rotations'] += 1
            
        except Exception as e:
            bot_logger.warning(f"Ошибка ротации логов: {e}")
    
    async def _cleanup_memory(self):
        """Принудительная очистка памяти"""
        try:
            # Принудительная сборка мусора
            before = gc.get_count()
            collected = gc.collect()
            after = gc.get_count()
            
            if collected > 0:
                bot_logger.info(f"🧹 Собрано {collected} объектов мусора")
                self.maintenance_stats['memory_cleanups'] += 1
            
        except Exception as e:
            bot_logger.warning(f"Ошибка очистки памяти: {e}")
    
    async def _check_system_resources(self):
        """Проверка системных ресурсов"""
        try:
            import psutil
            
            # Проверяем использование памяти
            memory = psutil.virtual_memory()
            if memory.percent > 80:
                bot_logger.warning(f"⚠️ Высокое использование памяти: {memory.percent:.1f}%")
            
            # Проверяем использование диска
            disk = psutil.disk_usage('/')
            if disk.percent > 85:
                bot_logger.warning(f"⚠️ Высокое использование диска: {disk.percent:.1f}%")
            
        except ImportError:
            # psutil не установлен, пропускаем проверку
            pass
        except Exception as e:
            bot_logger.warning(f"Ошибка проверки системных ресурсов: {e}")
    
    def stop_maintenance(self):
        """Остановка автоматического обслуживания"""
        self.running = False
        if self.maintenance_task and not self.maintenance_task.done():
            self.maintenance_task.cancel()
        bot_logger.info("🛑 Автоматическое обслуживание остановлено")
    
    def get_maintenance_stats(self) -> Dict[str, Any]:
        """Получение статистики обслуживания"""
        return {
            **self.maintenance_stats,
            'running': self.running,
            'next_maintenance': self.last_maintenance + self.maintenance_interval,
            'maintenance_interval_hours': self.maintenance_interval / 3600
        }
    
    async def force_maintenance(self):
        """Принудительное выполнение обслуживания"""
        bot_logger.info("🔧 Принудительное выполнение обслуживания...")
        await self._perform_maintenance()
        self.last_maintenance = time.time()


# Создаем глобальный экземпляр
auto_maintenance = AutoMaintenance()
