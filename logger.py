import logging
import os
import time
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler

class TradingBotLogger:
    def __init__(self, log_file: str = "trading_bot.log", max_size: int = 50*1024*1024, backup_count: int = 20):
        # Создаем папку logs если не существует
        self.logs_dir = "logs"
        if not os.path.exists(self.logs_dir):
            os.makedirs(self.logs_dir)
        
        # Устанавливаем путь к логу в папке logs
        self.log_file = os.path.join(self.logs_dir, log_file)
        self.logger = logging.getLogger('MEXCScalpingAssistant')
        self.logger.setLevel(logging.DEBUG)

        # Предотвращаем дублирование handlers
        if not self.logger.handlers:
            self._setup_handlers(max_size, backup_count)

    def _setup_handlers(self, max_size: int, backup_count: int):
        """Настраивает обработчики логов"""
        
        # Московское время (UTC+3)
        moscow_tz = timezone(timedelta(hours=3))
        
        class MoscowFormatter(logging.Formatter):
            def formatTime(self, record, datefmt=None):
                ct = datetime.fromtimestamp(record.created, tz=moscow_tz)
                if datefmt:
                    s = ct.strftime(datefmt)
                else:
                    s = ct.strftime('%Y-%m-%d %H:%M:%S MSK')
                return s
        
        formatter = MoscowFormatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S MSK'
        )

        # Файловый обработчик с ротацией
        try:
            # Принудительная ротация если файл уже существует и большой
            if os.path.exists(self.log_file) and os.path.getsize(self.log_file) > max_size * 0.8:
                # Переименовываем текущий файл
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                backup_name = os.path.join(self.logs_dir, f"trading_bot_{timestamp}.log.backup")
                try:
                    os.rename(self.log_file, backup_name)
                    print(f"Старый лог переименован в: {backup_name}")
                except Exception as rename_error:
                    print(f"Ошибка переименования лога: {rename_error}")
                    # Попробуем удалить файл если не можем переименовать
                    try:
                        os.remove(self.log_file)
                        print("Старый лог удален")
                    except Exception as remove_error:
                        print(f"Не удалось удалить лог: {remove_error}")
            
            file_handler = RotatingFileHandler(
                self.log_file,
                maxBytes=max_size,
                backupCount=backup_count,
                encoding='utf-8'
            )
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
            
            # Принудительная ротация если нужно
            if hasattr(file_handler, 'shouldRollover'):
                if file_handler.shouldRollover(logging.LogRecord("", 0, "", 0, "", (), None)):
                    file_handler.doRollover()
                    
        except Exception as e:
            print(f"Ошибка создания файлового логгера: {e}")
            # Создаем простой файловый handler без ротации как fallback
            try:
                fallback_log = os.path.join(self.logs_dir, f"bot_log_{int(time.time())}.log")
                simple_handler = logging.FileHandler(fallback_log, encoding='utf-8')
                simple_handler.setLevel(logging.INFO)
                simple_handler.setFormatter(formatter)
                self.logger.addHandler(simple_handler)
                print(f"Создан резервный лог: {fallback_log}")
            except Exception as e2:
                print(f"Критическая ошибка логирования: {e2}")

        # Консольный обработчик
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)

    def info(self, message: str):
        """Логирует информационное сообщение"""
        try:
            self.logger.info(message)
        except Exception as e:
            print(f"[LOG ERROR] info: {message}")

    def warning(self, message: str):
        """Логирует предупреждение"""
        try:
            self.logger.warning(message)
        except Exception as e:
            print(f"[LOG ERROR] warning: {message}")

    def error(self, message: str, exc_info: bool = False):
        """Логирует ошибку"""
        try:
            self.logger.error(message, exc_info=exc_info)
        except Exception as e:
            print(f"[LOG ERROR] error: {message}")

    def debug(self, message: str):
        """Логирует отладочное сообщение"""
        try:
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug(message)
        except Exception as e:
            print(f"[LOG ERROR] debug: {message}")

    def critical(self, message: str, exc_info: bool = False):
        """Логирует критическую ошибку"""
        self.logger.critical(message, exc_info=exc_info)

    def api_request(self, method: str, url: str, status_code: int, response_time: float):
        """Логирует API запрос (безопасно, без токенов)"""
        # Удаляем возможные токены из URL для безопасности
        safe_url = url.split('?')[0] if '?' in url else url
        if 'api.mexc.com' in safe_url:
            safe_url = safe_url.replace('https://api.mexc.com', 'MEXC_API')
        self.logger.info(f"API {method} {safe_url} - {status_code} ({response_time:.3f}s)")

    def trade_activity(self, symbol: str, action: str, details: str = ""):
        """Логирует торговую активность"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        message = f"[{timestamp}] {symbol} {action}"
        if details:
            message += f" - {details}"
        self.logger.info(message)

    def bot_action(self, action: str, details: str = ""):
        """Логирует действия бота"""
        message = f"BOT: {action}"
        if details:
            message += f" - {details}"
        self.logger.info(message)

    def performance_metric(self, metric_name: str, value: float, unit: str = ""):
        """Логирует метрики производительности"""
        message = f"METRIC: {metric_name} = {value}"
        if unit:
            message += f" {unit}"
        self.logger.info(message)

# Глобальный экземпляр логгера
bot_logger = TradingBotLogger()