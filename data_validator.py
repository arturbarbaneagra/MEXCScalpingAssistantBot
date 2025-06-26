
from typing import Dict, Any, Optional, List
from logger import bot_logger
import time

class DataValidator:
    """Валидатор для проверки данных API и пользовательского ввода"""
    
    @staticmethod
    def validate_symbol(symbol: str) -> bool:
        """Валидация символа монеты"""
        if not symbol or not isinstance(symbol, str):
            return False
        
        # Убираем USDT если есть
        clean_symbol = symbol.upper().replace("_USDT", "").replace("USDT", "")
        
        # Проверяем длину и символы
        if len(clean_symbol) < 2 or len(clean_symbol) > 10:
            return False
        
        # Только буквы и цифры
        return clean_symbol.replace(" ", "").replace("-", "").isalnum()

    @staticmethod
    def validate_coin_data(data: Dict[str, Any]) -> bool:
        """Валидация данных монеты от API"""
        required_fields = ['symbol', 'price', 'volume', 'change', 'spread', 'natr', 'trades', 'active']
        
        try:
            # Проверяем наличие всех полей
            for field in required_fields:
                if field not in data:
                    bot_logger.warning(f"Отсутствует поле {field} в данных монеты")
                    return False
            
            # Проверяем типы данных
            if not isinstance(data['symbol'], str) or len(data['symbol']) < 2:
                return False
                
            if not isinstance(data['price'], (int, float)) or data['price'] <= 0:
                return False
                
            if not isinstance(data['volume'], (int, float)) or data['volume'] < 0:
                return False
                
            if not isinstance(data['change'], (int, float)):
                return False
                
            if not isinstance(data['spread'], (int, float)) or data['spread'] < 0:
                return False
                
            if not isinstance(data['natr'], (int, float)) or data['natr'] < 0:
                return False
                
            if not isinstance(data['trades'], int) or data['trades'] < 0:
                return False
                
            if not isinstance(data['active'], bool):
                return False
            
            # Проверяем разумные границы
            if data['price'] > 1000000:  # Цена не должна быть слишком большой
                return False
                
            if data['volume'] > 10000000:  # Объем не должен быть слишком большим
                return False
                
            if abs(data['change']) > 1000:  # Изменение не должно быть больше 1000%
                return False
                
            return True
            
        except Exception as e:
            bot_logger.warning(f"Ошибка валидации данных монеты: {e}")
            return False

    @staticmethod
    def validate_config_value(key: str, value: Any) -> bool:
        """Валидация значений конфигурации"""
        config_rules = {
            'VOLUME_THRESHOLD': {'type': (int, float), 'min': 0, 'max': 1000000},
            'SPREAD_THRESHOLD': {'type': (int, float), 'min': 0, 'max': 100},
            'NATR_THRESHOLD': {'type': (int, float), 'min': 0, 'max': 100},
            'CHECK_BATCH_SIZE': {'type': int, 'min': 1, 'max': 50},
            'CHECK_BATCH_INTERVAL': {'type': (int, float), 'min': 0.1, 'max': 60},
            'INACTIVITY_TIMEOUT': {'type': int, 'min': 10, 'max': 3600}
        }
        
        if key not in config_rules:
            return True  # Неизвестные ключи пропускаем
        
        rules = config_rules[key]
        
        # Проверяем тип
        if not isinstance(value, rules['type']):
            return False
        
        # Проверяем границы
        if 'min' in rules and value < rules['min']:
            return False
            
        if 'max' in rules and value > rules['max']:
            return False
        
        return True

    @staticmethod
    def sanitize_user_input(text: str, max_length: int = 50) -> str:
        """Очистка пользовательского ввода"""
        if not text or not isinstance(text, str):
            return ""
        
        # Убираем лишние пробелы и ограничиваем длину
        sanitized = text.strip()[:max_length]
        
        # Убираем потенциально опасные символы
        dangerous_chars = ['<', '>', '&', '"', "'", '\\', '/', '\n', '\r', '\t']
        for char in dangerous_chars:
            sanitized = sanitized.replace(char, '')
        
        return sanitized

    @staticmethod
    def validate_api_response(response: Dict[str, Any], endpoint: str) -> bool:
        """Валидация ответа API"""
        try:
            if not isinstance(response, dict):
                return False
            
            # Специфические проверки для разных endpoint'ов
            if 'ticker' in endpoint:
                required_fields = ['symbol', 'lastPrice']
                return all(field in response for field in required_fields)
            
            elif 'klines' in endpoint:
                return isinstance(response, list) and len(response) > 0
            
            elif 'trades' in endpoint:
                return isinstance(response, list)
            
            elif 'bookTicker' in endpoint:
                required_fields = ['bidPrice', 'askPrice']
                return all(field in response for field in required_fields)
            
            return True
            
        except Exception as e:
            bot_logger.warning(f"Ошибка валидации API ответа для {endpoint}: {e}")
            return False

# Глобальный экземпляр валидатора
data_validator = DataValidator()
