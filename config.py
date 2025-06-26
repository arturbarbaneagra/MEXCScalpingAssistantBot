import json
import os
from typing import Dict, Any
from logger import bot_logger

class ConfigManager:
    def __init__(self, config_file: str = "config.json"):
        self.config_file = config_file
        self.default_config = {
            'VOLUME_THRESHOLD': 1000,
            'SPREAD_THRESHOLD': 0.1,
            'NATR_THRESHOLD': 0.5,
            'CHECK_BATCH_SIZE': 8,
            'CHECK_BATCH_INTERVAL': 1.0,
            'CHECK_FULL_CYCLE_INTERVAL': 60.0,
            'INACTIVITY_TIMEOUT': 120,
            'COIN_DATA_DELAY': 0.3,
            'MONITORING_UPDATE_INTERVAL': 30,
            'MAX_API_REQUESTS_PER_SECOND': 6,
            'MESSAGE_RATE_LIMIT': 2,
            'MAX_COINS_DISPLAY': 25,
            'API_TIMEOUT': 15,
            'MAX_RETRIES': 3
        }
        self.config = self.load()

    def load(self) -> Dict[str, Any]:
        """Загружает конфигурацию из файла"""
        if not os.path.exists(self.config_file):
            self.save(self.default_config)
            return self.default_config.copy()

        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # Проверяем и добавляем отсутствующие ключи
                updated = False
                for key, value in self.default_config.items():
                    if key not in config:
                        config[key] = value
                        updated = True

                if updated:
                    self.save(config)

                return config
        except (json.JSONDecodeError, IOError) as e:
            bot_logger.error(f"Ошибка загрузки конфига: {e}. Используется конфигурация по умолчанию.")
            self.save(self.default_config)
            return self.default_config.copy()

    def save(self, config: Dict[str, Any] = None) -> None:
        """Сохраняет конфигурацию в файл"""
        config_to_save = config if config is not None else self.config
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config_to_save, f, indent=2, ensure_ascii=False)
        except IOError as e:
            bot_logger.error(f"Ошибка сохранения конфига: {e}")

    def get(self, key: str, default=None):
        """Получает значение конфигурации"""
        return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Устанавливает значение конфигурации"""
        if key in self.default_config:
            self.config[key] = value
            self.save()
        else:
            raise KeyError(f"Неизвестный ключ конфигурации: {key}")

    def reset_to_default(self) -> None:
        """Сбрасывает конфигурацию к значениям по умолчанию"""
        self.config = self.default_config.copy()
        self.save()

# Глобальный экземпляр менеджера конфигурации
config_manager = ConfigManager()