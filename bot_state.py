
import json
import os
from typing import Dict, Optional
from datetime import datetime
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
        state['last_active_time'] = datetime.now().isoformat() if mode else None
        self.save_state(state)
    
    def get_last_mode(self) -> Optional[str]:
        """Получает последний режим работы"""
        state = self.load_state()
        return state.get('last_mode')
    
    def get_auto_start(self) -> bool:
        """Проверяет, нужно ли автоматически запускать бот"""
        state = self.load_state()
        return state.get('auto_start', False)

# Глобальный экземпляр менеджера состояния
bot_state_manager = BotStateManager()
import json
import os
from typing import Optional, Dict, Any
from logger import bot_logger

class BotStateManager:
    def __init__(self, state_file: str = "bot_state.json"):
        self.state_file = state_file
        self.state = self.load()

    def load(self) -> Dict[str, Any]:
        """Загружает состояние бота из файла"""
        if not os.path.exists(self.state_file):
            return {'last_mode': None, 'last_restart': None}

        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            bot_logger.error(f"Ошибка загрузки состояния бота: {e}")
            return {'last_mode': None, 'last_restart': None}

    def save(self) -> None:
        """Сохраняет состояние бота в файл"""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except IOError as e:
            bot_logger.error(f"Ошибка сохранения состояния бота: {e}")

    def get_last_mode(self) -> Optional[str]:
        """Получает последний режим работы бота"""
        return self.state.get('last_mode')

    def set_last_mode(self, mode: Optional[str]) -> None:
        """Устанавливает последний режим работы бота"""
        self.state['last_mode'] = mode
        self.save()

    def get_last_restart(self) -> Optional[float]:
        """Получает время последнего перезапуска"""
        return self.state.get('last_restart')

    def set_last_restart(self, timestamp: float) -> None:
        """Устанавливает время последнего перезапуска"""
        self.state['last_restart'] = timestamp
        self.save()

# Глобальный экземпляр
bot_state_manager = BotStateManager()
