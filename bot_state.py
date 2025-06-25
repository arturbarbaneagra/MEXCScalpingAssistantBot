
import json
import os
from typing import Optional, Dict
from logger import bot_logger

class BotStateManager:
    def __init__(self, state_file: str = "bot_state.json"):
        self.state_file = state_file
        self._default_state = {
            'last_mode': None,
            'auto_start': False,
            'last_active_time': None
        }
    
    def load_state(self) -> Dict:
        """Загружает состояние бота"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    return {**self._default_state, **state}
        except Exception as e:
            bot_logger.error(f"Ошибка загрузки состояния бота: {e}")
        
        return self._default_state.copy()
    
    def save_state(self, state: Dict):
        """Сохраняет состояние бота"""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            bot_logger.error(f"Ошибка сохранения состояния бота: {e}")
    
    def set_last_mode(self, mode: Optional[str]):
        """Сохраняет последний режим работы"""
        state = self.load_state()
        state['last_mode'] = mode
        state['auto_start'] = mode is not None
        self.save_state(state)
    
    def get_last_mode(self) -> Optional[str]:
        """Возвращает последний режим работы"""
        state = self.load_state()
        return state.get('last_mode')
    
    def should_auto_start(self) -> bool:
        """Проверяет, нужно ли автоматически запускать бот"""
        state = self.load_state()
        return state.get('auto_start', False)
    
    def clear_auto_start(self):
        """Отключает автозапуск"""
        state = self.load_state()
        state['auto_start'] = False
        state['last_mode'] = None
        self.save_state(state)

# Глобальный экземпляр
bot_state_manager = BotStateManager()
