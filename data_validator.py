"""
Модуль валидации данных для торгового бота
"""
import time
from typing import Dict, Any, Optional
from logger import bot_logger

class DataValidator:
    """Валидатор данных для обеспечения качества информации"""

    def __init__(self):
        self.validation_stats = {
            'total_validations': 0,
            'failed_validations': 0,
            'last_validation': 0
        }

    def validate_coin_data(self, data: Dict[str, Any]) -> bool:
        """Валидирует данные монеты"""
        self.validation_stats['total_validations'] += 1
        self.validation_stats['last_validation'] = time.time()

        try:
            # Проверяем обязательные поля
            required_fields = ['symbol', 'price', 'volume', 'change', 'spread', 'natr', 'trades', 'active']
            for field in required_fields:
                if field not in data:
                    bot_logger.warning(f"Отсутствует поле {field} в данных монеты")
                    self._record_failed_validation()
                    return False

            # Проверяем типы данных
            if not isinstance(data['symbol'], str) or len(data['symbol']) < 2:
                bot_logger.warning(f"Некорректный символ: {data.get('symbol')}")
                self._record_failed_validation()
                return False

            # Проверяем числовые значения
            numeric_fields = ['price', 'volume', 'change', 'spread', 'natr', 'trades']
            for field in numeric_fields:
                value = data[field]
                if not isinstance(value, (int, float)) or value < 0:
                    if field != 'change':  # change может быть отрицательным
                        bot_logger.warning(f"Некорректное значение {field}: {value}")
                        self._record_failed_validation()
                        return False

            # Проверяем разумные диапазоны
            if data['price'] > 1000000:  # Цена больше $1M
                bot_logger.warning(f"Подозрительно высокая цена для {data['symbol']}: {data['price']}")
                self._record_failed_validation()
                return False

            if data['spread'] > 50:  # Спред больше 50%
                bot_logger.warning(f"Подозрительно высокий спред для {data['symbol']}: {data['spread']}%")
                self._record_failed_validation()
                return False

            if data['natr'] > 100:  # NATR больше 100%
                bot_logger.warning(f"Подозрительно высокий NATR для {data['symbol']}: {data['natr']}%")
                self._record_failed_validation()
                return False

            # Проверяем булевые значения
            if not isinstance(data['active'], bool):
                bot_logger.warning(f"Некорректное значение active для {data['symbol']}: {data['active']}")
                self._record_failed_validation()
                return False

            return True

        except Exception as e:
            bot_logger.error(f"Ошибка валидации данных: {e}")
            self._record_failed_validation()
            return False

    def validate_api_response(self, response: Optional[Dict]) -> bool:
        """Валидирует ответ API"""
        if response is None:
            return False

        # Проверяем базовую структуру
        if not isinstance(response, dict):
            return False

        # Для ticker данных
        if 'symbol' in response and 'lastPrice' in response:
            try:
                float(response['lastPrice'])
                return True
            except (ValueError, TypeError):
                return False

        # Для klines данных
        if isinstance(response, list) and len(response) > 0:
            try:
                # Проверяем структуру первого элемента
                if isinstance(response[0], list) and len(response[0]) >= 8:
                    # Проверяем, что можем конвертировать в числа
                    float(response[0][1])  # open
                    float(response[0][4])  # close
                    float(response[0][7])  # volume
                    return True
            except (ValueError, TypeError, IndexError):
                return False

        return False

    def _record_failed_validation(self):
        """Записывает неудачную валидацию"""
        self.validation_stats['failed_validations'] += 1

    def get_validation_stats(self) -> Dict[str, Any]:
        """Возвращает статистику валидации"""
        total = self.validation_stats['total_validations']
        failed = self.validation_stats['failed_validations']

        return {
            'total_validations': total,
            'failed_validations': failed,
            'success_rate': ((total - failed) / total * 100) if total > 0 else 100,
            'last_validation': self.validation_stats['last_validation']
        }

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

# Глобальный экземпляр валидатора
data_validator = DataValidator()
```