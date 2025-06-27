
#!/usr/bin/env python3
"""
Система ротации логов для торгового бота
"""

import os
import time
import gzip
import shutil
from datetime import datetime, timedelta
from logger import bot_logger

class LogRotator:
    """Ротатор логов с сжатием и автоочисткой"""
    
    def __init__(self, log_dir: str = "logs", max_age_days: int = 7):
        self.log_dir = log_dir
        self.max_age_days = max_age_days
        
        # Создаем папку логов если не существует
        os.makedirs(log_dir, exist_ok=True)
    
    def rotate_logs(self):
        """Ротирует и сжимает старые логи"""
        try:
            current_time = time.time()
            cutoff_time = current_time - (self.max_age_days * 24 * 3600)
            
            log_files = [
                'trading_bot.log',
                'trading_bot.log.1',
                'trading_bot.log.2',
                'trading_bot.log.3',
                'trading_bot.log.4'
            ]
            
            compressed_count = 0
            deleted_count = 0
            
            for log_file in log_files:
                file_path = os.path.join(self.log_dir, log_file)
                
                if not os.path.exists(file_path):
                    continue
                
                file_time = os.path.getmtime(file_path)
                
                # Если файл старый
                if file_time < cutoff_time:
                    # Сжимаем если еще не сжат
                    if not log_file.endswith('.gz'):
                        compressed_path = f"{file_path}.gz"
                        try:
                            with open(file_path, 'rb') as f_in:
                                with gzip.open(compressed_path, 'wb') as f_out:
                                    shutil.copyfileobj(f_in, f_out)
                            os.remove(file_path)
                            compressed_count += 1
                        except Exception as e:
                            bot_logger.error(f"Ошибка сжатия {log_file}: {e}")
                    
                    # Удаляем очень старые сжатые файлы
                    gz_path = f"{file_path}.gz"
                    if os.path.exists(gz_path):
                        gz_time = os.path.getmtime(gz_path)
                        if gz_time < cutoff_time - (7 * 24 * 3600):  # Старше 14 дней
                            os.remove(gz_path)
                            deleted_count += 1
            
            if compressed_count > 0 or deleted_count > 0:
                bot_logger.info(f"Ротация логов: сжато {compressed_count}, удалено {deleted_count}")
                
        except Exception as e:
            bot_logger.error(f"Ошибка ротации логов: {e}")
    
    def get_log_stats(self) -> dict:
        """Возвращает статистику логов"""
        stats = {
            'total_files': 0,
            'total_size_mb': 0,
            'compressed_files': 0,
            'oldest_file': None
        }
        
        try:
            oldest_time = float('inf')
            
            for file_name in os.listdir(self.log_dir):
                if file_name.startswith('trading_bot.log'):
                    file_path = os.path.join(self.log_dir, file_name)
                    file_size = os.path.getsize(file_path)
                    file_time = os.path.getmtime(file_path)
                    
                    stats['total_files'] += 1
                    stats['total_size_mb'] += file_size / (1024 * 1024)
                    
                    if file_name.endswith('.gz'):
                        stats['compressed_files'] += 1
                    
                    if file_time < oldest_time:
                        oldest_time = file_time
                        stats['oldest_file'] = {
                            'name': file_name,
                            'age_days': (time.time() - file_time) / (24 * 3600)
                        }
        
        except Exception as e:
            bot_logger.error(f"Ошибка получения статистики логов: {e}")
        
        return stats
    
    def cleanup_old_logs(self, force_days: int = None):
        """Принудительная очистка старых логов"""
        days = force_days or self.max_age_days
        cutoff_time = time.time() - (days * 24 * 3600)
        
        try:
            removed_count = 0
            
            for file_name in os.listdir(self.log_dir):
                if file_name.startswith('trading_bot.log'):
                    file_path = os.path.join(self.log_dir, file_name)
                    file_time = os.path.getmtime(file_path)
                    
                    if file_time < cutoff_time:
                        os.remove(file_path)
                        removed_count += 1
            
            if removed_count > 0:
                bot_logger.info(f"Принудительно удалено {removed_count} старых логов")
                
        except Exception as e:
            bot_logger.error(f"Ошибка принудительной очистки логов: {e}")

# Глобальный экземпляр ротатора
log_rotator = LogRotator()
