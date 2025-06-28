import json
import os
from typing import Dict, Any
from logger import bot_logger

class ConfigManager:
    def __init__(self, config_file: str = "config.json"):
        self.config_file = config_file
        self.config: Dict[str, Any] = {}
        self.default_config = {
            "VOLUME_THRESHOLD": 1500,
            "SPREAD_THRESHOLD": 0.1,
            "NATR_THRESHOLD": 0.4,
            "CHECK_BATCH_SIZE": 15,
            "CHECK_BATCH_INTERVAL": 0.4,
            "CHECK_FULL_CYCLE_INTERVAL": 1.0,
            "INACTIVITY_TIMEOUT": 30,
            "COIN_DATA_DELAY": 0.1,
            "MONITORING_UPDATE_INTERVAL": 8,
            "MAX_API_REQUESTS_PER_SECOND": 12,
            "MESSAGE_RATE_LIMIT": 1.0,
            "MAX_COINS_DISPLAY": 30,
            "API_TIMEOUT": 10,
            "MAX_RETRIES": 2
        }
        self.load()

    def load(self):
        """Загружает конфигурацию из файла с валидацией"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    file_config = json.load(f)
                    # Объединяем с дефолтными значениями
                    self.config = {**self.default_config, **file_config}
                    
                    # Валидируем и исправляем конфигурацию
                    try:
                        from config_validator import config_validator
                        fixed_config, errors, recommendations = config_validator.validate_and_fix(self.config)
                        
                        if errors:
                            bot_logger.warning(f"Ошибки в конфигурации: {'; '.join(errors)}")
                        
                        if recommendations:
                            bot_logger.info(f"Рекомендации по конфигурации: {'; '.join(recommendations[:3])}")
                        
                        # Применяем исправления если были изменения
                        if fixed_config != self.config:
                            self.config = fixed_config
                            self.save()
                            bot_logger.info("Конфигурация автоматически исправлена и сохранена")
                        
                    except ImportError:
                        bot_logger.debug("Config validator недоступен, пропускаем валидацию")
                    
                    bot_logger.info("Конфигурация загружена из файла")
            else:
                self.config = self.default_config.copy()
                self.save()
                bot_logger.info("Создана конфигурация по умолчанию")
        except Exception as e:
            bot_logger.error(f"Ошибка загрузки конфигурации: {e}")
            self.config = self.default_config.copy()

    def save(self):
        """Сохраняет конфигурацию в файл"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            bot_logger.debug("Конфигурация сохранена")
        except Exception as e:
            bot_logger.error(f"Ошибка сохранения конфигурации: {e}")

    def get(self, key: str, default=None) -> Any:
        """Получает значение конфигурации"""
        return self.config.get(key, default)

    def set(self, key: str, value: Any):
        """Устанавливает значение конфигурации"""
        self.config[key] = value
        self.save()
        bot_logger.debug(f"Параметр {key} установлен в {value}")

    def reset_to_defaults(self):
        """Сбрасывает конфигурацию к значениям по умолчанию"""
        self.config = self.default_config.copy()
        self.save()
        bot_logger.info("Конфигурация сброшена к значениям по умолчанию")

    def get_all(self) -> Dict[str, Any]:
        """Возвращает всю конфигурацию"""
        return self.config.copy()

# Глобальный экземпляр менеджера конфигурации
config_manager = ConfigManager()