import json
import os
import asyncio
from typing import Dict, Any
from logger import bot_logger

class ConfigManager:
    def __init__(self, config_file: str = "config.json"):
        self.config_file = config_file
        self.config: Dict[str, Any] = {}
        self.change_callbacks = []  # Список callback функций для уведомления об изменениях
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
        """Загружает конфигурацию из файла"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    file_config = json.load(f)
                    # Объединяем с дефолтными значениями
                    self.config = {**self.default_config, **file_config}
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
        old_value = self.config.get(key)
        self.config[key] = value
        self.save()
        bot_logger.debug(f"Параметр {key} установлен в {value}")
        
        # Уведомляем о изменении если значение действительно изменилось
        if old_value != value:
            self._notify_change(key, old_value, value)

    def add_change_callback(self, callback):
        """Добавляет callback для уведомления об изменениях"""
        if callback not in self.change_callbacks:
            self.change_callbacks.append(callback)

    def remove_change_callback(self, callback):
        """Удаляет callback"""
        if callback in self.change_callbacks:
            self.change_callbacks.remove(callback)

    def _notify_change(self, key: str, old_value: Any, new_value: Any):
        """Уведомляет все callback'и об изменении"""
        for callback in self.change_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    # Для async callback'ов планируем выполнение
                    asyncio.create_task(callback(key, old_value, new_value))
                else:
                    # Для обычных функций вызываем напрямую
                    callback(key, old_value, new_value)
            except Exception as e:
                bot_logger.error(f"Ошибка в callback изменения конфигурации: {e}")

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