import json
import os
from typing import Dict, Optional
from datetime import datetime
from logger import bot_logger

class BotStateManager:
    def __init__(self, state_file: str = "bot_state.json"):
        self.state_file = state_file
        self.state: Dict = {}
        self.load()

    def load(self):
        """Загружает состояние бота из файла"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    self.state = json.load(f)
                    bot_logger.debug("Состояние бота загружено")
            else:
                self.state = {
                    'last_mode': None,
                    'last_active': None,
                    'session_count': 0
                }
                self.save()
                bot_logger.debug("Создано новое состояние бота")
        except Exception as e:
            bot_logger.error(f"Ошибка загрузки состояния бота: {e}")
            self.state = {
                'last_mode': None,
                'last_active': None,
                'session_count': 0
            }

    def save(self):
        """Сохраняет состояние бота в файл"""
        try:
            self.state['last_updated'] = datetime.now().isoformat()
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            bot_logger.error(f"Ошибка сохранения состояния бота: {e}")

    def get_last_mode(self) -> Optional[str]:
        """Получает последний режим работы бота"""
        return self.state.get('last_mode')

    def set_last_mode(self, mode: Optional[str]):
        """Устанавливает последний режим работы бота"""
        self.state['last_mode'] = mode
        self.state['last_active'] = datetime.now().isoformat() if mode else None
        self.save()

    def increment_session(self):
        """Увеличивает счетчик сессий"""
        self.state['session_count'] = self.state.get('session_count', 0) + 1
        self.save()

    def get_session_count(self) -> int:
        """Получает количество сессий"""
        return self.state.get('session_count', 0)

    def clear_state(self):
        """Очищает состояние бота"""
        self.state = {
            'last_mode': None,
            'last_active': None,
            'session_count': 0
        }
        self.save()

# Глобальный экземпляр менеджера состояния
bot_state_manager = BotStateManager()