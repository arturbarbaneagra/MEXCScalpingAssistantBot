
"""
Модуль валидации входных данных для торгового бота
"""

import re
from typing import Optional

class InputValidator:
    """Валидатор входных данных"""
    
    @staticmethod
    def validate_symbol(symbol: str) -> bool:
        """Валидация символа криптовалюты"""
        if not symbol or not isinstance(symbol, str):
            return False
        
        # Убираем _USDT и USDT
        clean_symbol = symbol.upper().replace("_USDT", "").replace("USDT", "")
        
        # Проверяем формат
        if not re.match(r'^[A-Z0-9]{2,10}$', clean_symbol):
            return False
            
        # Запрещенные символы
        forbidden = ['XXX', 'NULL', 'UNDEFINED']
        if clean_symbol in forbidden:
            return False
            
        return True
    
    @staticmethod
    def validate_numeric_input(value: str, min_val: float = 0, max_val: float = None) -> Optional[float]:
        """Валидация числового ввода"""
        try:
            num_value = float(value)
            if num_value < min_val:
                return None
            if max_val is not None and num_value > max_val:
                return None
            return num_value
        except (ValueError, TypeError):
            return None
    
    @staticmethod
    def sanitize_text(text: str, max_length: int = 100) -> str:
        """Санитизация текстового ввода"""
        if not text or not isinstance(text, str):
            return ""
        
        # Удаляем HTML теги
        clean_text = re.sub(r'<[^>]+>', '', text)
        
        # Ограничиваем длину
        if len(clean_text) > max_length:
            clean_text = clean_text[:max_length]
        
        return clean_text.strip()

# Глобальный экземпляр
input_validator = InputValidator()
