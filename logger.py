
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

class TradingBotLogger:
    def __init__(self, log_file: str = "trading_bot.log", max_size: int = 10*1024*1024, backup_count: int = 5):
        self.log_file = log_file
        self.logger = logging.getLogger('TradingBot')
        self.logger.setLevel(logging.DEBUG)
        
        # Предотвращаем дублирование handlers
        if not self.logger.handlers:
            self._setup_handlers(max_size, backup_count)
    
    def _setup_handlers(self, max_size: int, backup_count: int):
        """Настраивает обработчики логов"""
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Файловый обработчик с ротацией
        try:
            file_handler = RotatingFileHandler(
                self.log_file,
                maxBytes=max_size,
                backupCount=backup_count,
                encoding='utf-8'
            )
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
        except Exception as e:
            print(f"Ошибка создания файлового логгера: {e}")
        
        # Консольный обработчик
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
    
    def info(self, message: str):
        """Логирует информационное сообщение"""
        self.logger.info(message)
    
    def warning(self, message: str):
        """Логирует предупреждение"""
        self.logger.warning(message)
    
    def error(self, message: str, exc_info: bool = False):
        """Логирует ошибку"""
        self.logger.error(message, exc_info=exc_info)
    
    def debug(self, message: str):
        """Логирует отладочное сообщение"""
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(message)
    
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
