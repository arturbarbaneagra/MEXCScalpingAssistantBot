
"""
Валидатор конфигурации MEXCScalping Assistant
"""

from typing import Dict, Any, List, Tuple
from logger import bot_logger

class ConfigValidator:
    """Валидатор настроек конфигурации"""
    
    def __init__(self):
        self.validation_rules = {
            'VOLUME_THRESHOLD': {'min': 100, 'max': 1000000, 'type': (int, float)},
            'SPREAD_THRESHOLD': {'min': 0.01, 'max': 10.0, 'type': (int, float)},
            'NATR_THRESHOLD': {'min': 0.1, 'max': 20.0, 'type': (int, float)},
            'CHECK_BATCH_SIZE': {'min': 1, 'max': 50, 'type': int},
            'CHECK_BATCH_INTERVAL': {'min': 0.1, 'max': 5.0, 'type': (int, float)},
            'API_TIMEOUT': {'min': 5, 'max': 60, 'type': int},
            'MAX_RETRIES': {'min': 1, 'max': 10, 'type': int}
        }
    
    def validate_config(self, config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Валидирует конфигурацию"""
        errors = []
        
        for key, rules in self.validation_rules.items():
            if key in config:
                value = config[key]
                
                # Проверка типа
                if not isinstance(value, rules['type']):
                    errors.append(f"{key}: неверный тип (ожидается {rules['type']})")
                    continue
                
                # Проверка диапазона
                if 'min' in rules and value < rules['min']:
                    errors.append(f"{key}: значение {value} меньше минимального {rules['min']}")
                
                if 'max' in rules and value > rules['max']:
                    errors.append(f"{key}: значение {value} больше максимального {rules['max']}")
        
        # Логические проверки
        if 'VOLUME_THRESHOLD' in config and 'SPREAD_THRESHOLD' in config:
            if config['VOLUME_THRESHOLD'] < 500 and config['SPREAD_THRESHOLD'] < 0.1:
                errors.append("Слишком агрессивные настройки: низкий объем + низкий спред")
        
        return len(errors) == 0, errors
    
    def suggest_optimizations(self, config: Dict[str, Any]) -> List[str]:
        """Предлагает оптимизации конфигурации"""
        suggestions = []
        
        # Анализ настроек для скальпинга
        if config.get('VOLUME_THRESHOLD', 0) > 10000:
            suggestions.append("Рассмотрите снижение VOLUME_THRESHOLD для большего количества сигналов")
        
        if config.get('SPREAD_THRESHOLD', 0) > 0.5:
            suggestions.append("Высокий SPREAD_THRESHOLD может пропускать хорошие возможности")
        
        batch_size = config.get('CHECK_BATCH_SIZE', 15)
        batch_interval = config.get('CHECK_BATCH_INTERVAL', 0.4)
        
        if batch_size > 20 and batch_interval < 0.5:
            suggestions.append("Высокая нагрузка на API: уменьшите batch_size или увеличьте interval")
        
        return suggestions

# Глобальный экземпляр
config_validator = ConfigValidator()
"""
Валидатор конфигурации
"""

from typing import Dict, Any, List
from logger import bot_logger

class ConfigValidator:
    def __init__(self):
        self.required_fields = [
            'VOLUME_THRESHOLD',
            'SPREAD_THRESHOLD',
            'NATR_THRESHOLD'
        ]
        
        self.field_validators = {
            'VOLUME_THRESHOLD': lambda x: isinstance(x, (int, float)) and x > 0,
            'SPREAD_THRESHOLD': lambda x: isinstance(x, (int, float)) and 0 <= x <= 100,
            'NATR_THRESHOLD': lambda x: isinstance(x, (int, float)) and 0 <= x <= 100,
        }

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Валидирует конфигурацию"""
        errors = []
        
        # Проверяем обязательные поля
        for field in self.required_fields:
            if field not in config:
                errors.append(f"Отсутствует обязательное поле: {field}")
                continue
                
            # Проверяем валидность значения
            validator = self.field_validators.get(field)
            if validator and not validator(config[field]):
                errors.append(f"Неверное значение для {field}: {config[field]}")
        
        return errors

    def is_valid_config(self, config: Dict[str, Any]) -> bool:
        """Проверяет валидность конфигурации"""
        return len(self.validate_config(config)) == 0

# Глобальный экземпляр
config_validator = ConfigValidator()
