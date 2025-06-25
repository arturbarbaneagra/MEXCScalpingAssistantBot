
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

class TradingBotLogger:
    def __init__(self, log_dir="logs"):
        self.log_dir = log_dir
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        # Основной логгер
        self.logger = logging.getLogger('TradingBot')
        self.logger.setLevel(logging.INFO)
        
        # Логгер для ошибок
        self.error_logger = logging.getLogger('TradingBot.Errors')
        self.error_logger.setLevel(logging.ERROR)
        
        # Логгер для API запросов
        self.api_logger = logging.getLogger('TradingBot.API')
        self.api_logger.setLevel(logging.INFO)
        
        self._setup_handlers()
    
    def _setup_handlers(self):
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # Общий лог файл (ротация по размеру)
        general_handler = RotatingFileHandler(
            os.path.join(self.log_dir, 'bot.log'),
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
        general_handler.setFormatter(formatter)
        self.logger.addHandler(general_handler)
        
        # Файл ошибок
        error_handler = RotatingFileHandler(
            os.path.join(self.log_dir, 'errors.log'),
            maxBytes=5*1024*1024,  # 5MB
            backupCount=3
        )
        error_handler.setFormatter(formatter)
        self.error_logger.addHandler(error_handler)
        
        # API логи
        api_handler = RotatingFileHandler(
            os.path.join(self.log_dir, 'api.log'),
            maxBytes=10*1024*1024,  # 10MB
            backupCount=3
        )
        api_handler.setFormatter(formatter)
        self.api_logger.addHandler(api_handler)
        
        # Консольный вывод
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.INFO)
        self.logger.addHandler(console_handler)
    
    def info(self, message):
        self.logger.info(message)
    
    def error(self, message, exc_info=None):
        self.logger.error(message, exc_info=exc_info)
        self.error_logger.error(message, exc_info=exc_info)
    
    def warning(self, message):
        self.logger.warning(message)
    
    def api_request(self, method, url, response_code, response_time=None):
        msg = f"{method} {url} - {response_code}"
        if response_time:
            msg += f" ({response_time:.2f}s)"
        self.api_logger.info(msg)
    
    def trade_activity(self, symbol, activity_type, data):
        msg = f"{symbol} - {activity_type}: {data}"
        self.logger.info(msg)

# Глобальный экземпляр логгера
bot_logger = TradingBotLogger()
