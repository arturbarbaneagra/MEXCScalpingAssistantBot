
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
    def __init__(self, log_dir="logs", max_size_mb=50, max_files=10, compress_old=True):
        self.log_dir = log_dir
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.max_files = max_files
        self.compress_old = compress_old
        
        # Создаем директорию если не существует
        os.makedirs(log_dir, exist_ok=True)
    
    def should_rotate(self, log_file_path):
        """Проверяет нужна ли ротация лога"""
        try:
            if not os.path.exists(log_file_path):
                return False
            
            # Проверяем размер файла
            file_size = os.path.getsize(log_file_path)
            return file_size >= self.max_size_bytes
        except Exception as e:
            bot_logger.error(f"Ошибка проверки ротации лога: {e}")
            return False
    
    def rotate_log(self, log_file_path):
        """Выполняет ротацию лог файла"""
        try:
            if not os.path.exists(log_file_path):
                return
            
            base_name = os.path.splitext(log_file_path)[0]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Новое имя для ротированного файла
            rotated_name = f"{base_name}_{timestamp}.log"
            
            # Перемещаем текущий лог
            shutil.move(log_file_path, rotated_name)
            
            # Сжимаем если нужно
            if self.compress_old:
                self._compress_file(rotated_name)
            
            # Очищаем старые файлы
            self._cleanup_old_logs(base_name)
            
            bot_logger.info(f"Лог ротирован: {rotated_name}")
            
        except Exception as e:
            bot_logger.error(f"Ошибка ротации лога: {e}")
    
    def _compress_file(self, file_path):
        """Сжимает лог файл"""
        try:
            compressed_path = f"{file_path}.gz"
            with open(file_path, 'rb') as f_in:
                with gzip.open(compressed_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            
            # Удаляем несжатый файл
            os.remove(file_path)
            
        except Exception as e:
            bot_logger.error(f"Ошибка сжатия файла {file_path}: {e}")
    
    def _cleanup_old_logs(self, base_name):
        """Удаляет старые лог файлы"""
        try:
            log_files = []
            
            # Находим все файлы логов
            for file in os.listdir(self.log_dir):
                if file.startswith(os.path.basename(base_name)) and file != os.path.basename(base_name):
                    file_path = os.path.join(self.log_dir, file)
                    if os.path.isfile(file_path):
                        log_files.append((file_path, os.path.getmtime(file_path)))
            
            # Сортируем по времени модификации
            log_files.sort(key=lambda x: x[1], reverse=True)
            
            # Удаляем лишние файлы
            for file_path, _ in log_files[self.max_files:]:
                os.remove(file_path)
                bot_logger.debug(f"Удален старый лог: {file_path}")
                
        except Exception as e:
            bot_logger.error(f"Ошибка очистки старых логов: {e}")
    
    def cleanup_by_age(self, max_days=30):
        """Удаляет логи старше указанного количества дней"""
        try:
            cutoff_time = time.time() - (max_days * 24 * 3600)
            
            for file in os.listdir(self.log_dir):
                file_path = os.path.join(self.log_dir, file)
                if os.path.isfile(file_path) and os.path.getmtime(file_path) < cutoff_time:
                    os.remove(file_path)
                    bot_logger.debug(f"Удален устаревший лог: {file_path}")
                    
        except Exception as e:
            bot_logger.error(f"Ошибка очистки устаревших логов: {e}")

# Глобальный экземпляр
log_rotator = LogRotator()
