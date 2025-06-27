
"""
Модуль управления состоянием MEXCScalping Assistant
"""

import json
import os
import time
from typing import Dict, Any, Optional
from logger import bot_logger

class BotStateManager:
    """Менеджер состояния бота для сохранения между перезапусками"""
    
    def __init__(self, state_file: str = "bot_state.json"):
        self.state_file = state_file
        self.state: Dict[str, Any] = {}
        self.load()

    def load(self):
        """Загружает состояние из файла"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    self.state = json.load(f)
                    bot_logger.info("Состояние бота загружено")
            else:
                self.state = {
                    'last_mode': None,
                    'last_active_time': 0,
                    'session_count': 0,
                    'total_uptime': 0,
                    'created_at': time.time()
                }
                bot_logger.info("Создано новое состояние бота")
        except Exception as e:
            bot_logger.error(f"Ошибка загрузки состояния: {e}")
            self.state = {
                'last_mode': None,
                'last_active_time': 0,
                'session_count': 0,
                'total_uptime': 0,
                'created_at': time.time()
            }

    def save(self):
        """Сохраняет состояние в файл"""
        try:
            self.state['last_saved'] = time.time()
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
            bot_logger.debug("Состояние бота сохранено")
        except Exception as e:
            bot_logger.error(f"Ошибка сохранения состояния: {e}")

    def set_last_mode(self, mode: Optional[str]):
        """Устанавливает последний режим работы"""
        self.state['last_mode'] = mode
        self.state['last_active_time'] = time.time() if mode else 0
        self.save()

    def get_last_mode(self) -> Optional[str]:
        """Возвращает последний режим работы"""
        return self.state.get('last_mode')

    def increment_session(self):
        """Увеличивает счетчик сессий"""
        self.state['session_count'] = self.state.get('session_count', 0) + 1
        self.save()

    def add_uptime(self, seconds: float):
        """Добавляет время работы"""
        self.state['total_uptime'] = self.state.get('total_uptime', 0) + seconds
        self.save()

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику состояния"""
        current_time = time.time()
        created_at = self.state.get('created_at', current_time)
        
        return {
            'last_mode': self.state.get('last_mode'),
            'last_active_time': self.state.get('last_active_time', 0),
            'session_count': self.state.get('session_count', 0),
            'total_uptime_hours': self.state.get('total_uptime', 0) / 3600,
            'days_since_creation': (current_time - created_at) / 86400,
            'last_saved': self.state.get('last_saved', 0)
        }

    def clear_state(self):
        """Очищает состояние"""
        self.state = {
            'last_mode': None,
            'last_active_time': 0,
            'session_count': 0,
            'total_uptime': 0,
            'created_at': time.time()
        }
        self.save()
        bot_logger.info("Состояние бота очищено")

# Глобальный экземпляр менеджера состояния
bot_state_manager = BotStateManager()
