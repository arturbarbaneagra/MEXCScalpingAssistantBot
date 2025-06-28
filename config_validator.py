
"""
Валидатор конфигурации MEXCScalping Assistant
"""

from typing import Dict, Any, List, Tuple
from logger import bot_logger

class ConfigValidator:
    """Валидатор настроек конфигурации"""
    
    def __init__(self):
        self.validation_rules = {
            'VOLUME_THRESHOLD': {
                'type': (int, float),
                'min': 100,
                'max': 100000,
                'description': 'Минимальный объём в USDT'
            },
            'SPREAD_THRESHOLD': {
                'type': (int, float),
                'min': 0.01,
                'max': 10.0,
                'description': 'Минимальный спред в %'
            },
            'NATR_THRESHOLD': {
                'type': (int, float),
                'min': 0.1,
                'max': 20.0,
                'description': 'Минимальный NATR в %'
            },
            'CHECK_BATCH_SIZE': {
                'type': int,
                'min': 1,
                'max': 50,
                'description': 'Размер батча для проверки'
            },
            'CHECK_BATCH_INTERVAL': {
                'type': (int, float),
                'min': 0.1,
                'max': 5.0,
                'description': 'Интервал между батчами в секундах'
            },
            'API_TIMEOUT': {
                'type': int,
                'min': 5,
                'max': 60,
                'description': 'Таймаут API запросов в секундах'
            },
            'MAX_RETRIES': {
                'type': int,
                'min': 1,
                'max': 10,
                'description': 'Максимальное количество повторов'
            },
            'CACHE_TTL_SECONDS': {
                'type': int,
                'min': 1,
                'max': 60,
                'description': 'Время жизни кеша в секундах'
            }
        }
    
    def validate_config(self, config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Валидирует конфигурацию
        Возвращает (is_valid, errors)
        """
        errors = []
        
        for key, value in config.items():
            if key in self.validation_rules:
                rule = self.validation_rules[key]
                
                # Проверка типа
                if not isinstance(value, rule['type']):
                    expected_type = rule['type'].__name__ if hasattr(rule['type'], '__name__') else str(rule['type'])
                    errors.append(f"{key}: ожидается {expected_type}, получено {type(value).__name__}")
                    continue
                
                # Проверка диапазона
                if 'min' in rule and value < rule['min']:
                    errors.append(f"{key}: значение {value} меньше минимального {rule['min']}")
                
                if 'max' in rule and value > rule['max']:
                    errors.append(f"{key}: значение {value} больше максимального {rule['max']}")
        
        # Проверка логических связей между параметрами
        logical_errors = self._validate_logical_relationships(config)
        errors.extend(logical_errors)
        
        return len(errors) == 0, errors
    
    def _validate_logical_relationships(self, config: Dict[str, Any]) -> List[str]:
        """Проверяет логические связи между параметрами"""
        errors = []
        
        # Batch size не должен быть слишком большим при маленьком интервале
        batch_size = config.get('CHECK_BATCH_SIZE', 10)
        batch_interval = config.get('CHECK_BATCH_INTERVAL', 0.5)
        
        if batch_size > 20 and batch_interval < 0.3:
            errors.append("CHECK_BATCH_SIZE слишком большой для такого маленького CHECK_BATCH_INTERVAL")
        
        # Таймаут должен быть больше интервала батча
        api_timeout = config.get('API_TIMEOUT', 10)
        if api_timeout < batch_interval * 2:
            errors.append("API_TIMEOUT должен быть как минимум в 2 раза больше CHECK_BATCH_INTERVAL")
        
        # Проверяем разумность порогов
        volume_threshold = config.get('VOLUME_THRESHOLD', 1000)
        spread_threshold = config.get('SPREAD_THRESHOLD', 0.1)
        
        if volume_threshold > 50000 and spread_threshold < 0.05:
            errors.append("Высокий VOLUME_THRESHOLD с низким SPREAD_THRESHOLD может дать мало результатов")
        
        return errors
    
    def get_recommendations(self, config: Dict[str, Any]) -> List[str]:
        """Возвращает рекомендации по оптимизации конфигурации"""
        recommendations = []
        
        # Рекомендации по производительности
        batch_size = config.get('CHECK_BATCH_SIZE', 10)
        batch_interval = config.get('CHECK_BATCH_INTERVAL', 0.5)
        
        if batch_size < 10:
            recommendations.append("Рассмотрите увеличение CHECK_BATCH_SIZE до 10-15 для лучшей производительности")
        
        if batch_interval > 1.0:
            recommendations.append("CHECK_BATCH_INTERVAL можно уменьшить до 0.5-0.8 для более быстрых обновлений")
        
        # Рекомендации по фильтрам
        volume_threshold = config.get('VOLUME_THRESHOLD', 1000)
        if volume_threshold < 500:
            recommendations.append("VOLUME_THRESHOLD ниже $500 может давать много ложных сигналов")
        
        spread_threshold = config.get('SPREAD_THRESHOLD', 0.1)
        if spread_threshold > 0.5:
            recommendations.append("SPREAD_THRESHOLD выше 0.5% может исключить хорошие возможности для скальпинга")
        
        # Рекомендации по кешу
        cache_ttl = config.get('CACHE_TTL_SECONDS', 5)
        if cache_ttl > 10:
            recommendations.append("CACHE_TTL_SECONDS выше 10 секунд может приводить к устаревшим данным")
        
        return recommendations
    
    def auto_fix_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Автоматически исправляет явные проблемы в конфигурации"""
        fixed_config = config.copy()
        fixes_applied = []
        
        for key, value in fixed_config.items():
            if key in self.validation_rules:
                rule = self.validation_rules[key]
                
                # Исправляем тип
                if not isinstance(value, rule['type']):
                    if rule['type'] == int:
                        try:
                            fixed_config[key] = int(value)
                            fixes_applied.append(f"{key}: преобразован в int")
                        except (ValueError, TypeError):
                            fixed_config[key] = self._get_default_value(key)
                            fixes_applied.append(f"{key}: установлено значение по умолчанию")
                    elif rule['type'] == (int, float):
                        try:
                            fixed_config[key] = float(value)
                            fixes_applied.append(f"{key}: преобразован в float")
                        except (ValueError, TypeError):
                            fixed_config[key] = self._get_default_value(key)
                            fixes_applied.append(f"{key}: установлено значение по умолчанию")
                
                # Исправляем диапазон
                if 'min' in rule and fixed_config[key] < rule['min']:
                    fixed_config[key] = rule['min']
                    fixes_applied.append(f"{key}: увеличено до минимального значения {rule['min']}")
                
                if 'max' in rule and fixed_config[key] > rule['max']:
                    fixed_config[key] = rule['max']
                    fixes_applied.append(f"{key}: уменьшено до максимального значения {rule['max']}")
        
        if fixes_applied:
            bot_logger.info(f"Автоисправления конфигурации: {'; '.join(fixes_applied)}")
        
        return fixed_config
    
    def _get_default_value(self, key: str) -> Any:
        """Возвращает значение по умолчанию для ключа"""
        defaults = {
            'VOLUME_THRESHOLD': 1000,
            'SPREAD_THRESHOLD': 0.1,
            'NATR_THRESHOLD': 0.5,
            'CHECK_BATCH_SIZE': 15,
            'CHECK_BATCH_INTERVAL': 0.4,
            'API_TIMEOUT': 10,
            'MAX_RETRIES': 2,
            'CACHE_TTL_SECONDS': 5
        }
        return defaults.get(key, 0)
    
    def validate_and_fix(self, config: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str], List[str]]:
        """
        Валидирует и исправляет конфигурацию
        Возвращает (fixed_config, errors, recommendations)
        """
        # Сначала пытаемся исправить
        fixed_config = self.auto_fix_config(config)
        
        # Затем валидируем исправленную конфигурацию
        is_valid, errors = self.validate_config(fixed_config)
        
        # Получаем рекомендации
        recommendations = self.get_recommendations(fixed_config)
        
        return fixed_config, errors, recommendations

# Глобальный экземпляр валидатора
config_validator = ConfigValidator()
