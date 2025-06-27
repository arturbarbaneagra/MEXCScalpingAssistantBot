
import time
import json
import os
from typing import Dict, Any, Optional
from logger import bot_logger

class BotStateManager:
    """Менеджер состояния бота для отслеживания статистики и сессий"""
    
    def __init__(self):
        self.state_file = 'bot_state.json'
        self.current_session_start = time.time()
        self.state_data = self._load_state()
        self.session_id = int(time.time())
        
    def _load_state(self) -> Dict[str, Any]:
        """Загружает состояние из файла"""
        # Состояние по умолчанию
        default_state = {
            'total_sessions': 0,
            'total_uptime': 0.0,
            'last_session': None,
            'statistics': {
                'successful_starts': 0,
                'failed_starts': 0,
                'average_uptime': 0.0
            }
        }
        
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    loaded_state = json.load(f)
                    # Объединяем загруженное состояние с дефолтным
                    # чтобы гарантировать наличие всех ключей
                    for key, value in default_state.items():
                        if key not in loaded_state:
                            loaded_state[key] = value
                        elif key == 'statistics' and isinstance(value, dict):
                            # Убеждаемся, что все ключи статистики присутствуют
                            for stat_key, stat_value in value.items():
                                if stat_key not in loaded_state[key]:
                                    loaded_state[key][stat_key] = stat_value
                    return loaded_state
        except Exception as e:
            bot_logger.warning(f"Не удалось загрузить состояние: {e}")
        
        # Возвращаем состояние по умолчанию
        return default_state
    
    def _save_state(self):
        """Сохраняет состояние в файл"""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.state_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            bot_logger.error(f"Ошибка сохранения состояния: {e}")
    
    def increment_session(self):
        """Увеличивает счетчик сессий и отмечает успешный запуск"""
        try:
            # Безопасное увеличение счетчика
            self.state_data['total_sessions'] = self.state_data.get('total_sessions', 0) + 1
            
            # Убеждаемся, что statistics существует
            if 'statistics' not in self.state_data:
                self.state_data['statistics'] = {
                    'successful_starts': 0,
                    'failed_starts': 0,
                    'average_uptime': 0.0
                }
            
            self.state_data['statistics']['successful_starts'] = self.state_data['statistics'].get('successful_starts', 0) + 1
            
            self.state_data['last_session'] = {
                'session_id': self.session_id,
                'start_time': self.current_session_start,
                'status': 'running'
            }
            
            self._save_state()
            bot_logger.info(f"Начата сессия #{self.state_data['total_sessions']} (ID: {self.session_id})")
            
        except Exception as e:
            bot_logger.error(f"Ошибка при увеличении счетчика сессий: {e}")
            # Пересоздаем состояние с нуля
            self.state_data = self._load_state()
            bot_logger.info("Состояние пересоздано, повторная попытка...")
            self.increment_session()
    
    def add_uptime(self, uptime_seconds: float):
        """Добавляет время работы к общей статистике"""
        self.state_data['total_uptime'] += uptime_seconds
        
        # Обновляем среднее время работы
        total_sessions = self.state_data['total_sessions']
        if total_sessions > 0:
            self.state_data['statistics']['average_uptime'] = self.state_data['total_uptime'] / total_sessions
        
        # Обновляем информацию о текущей сессии
        if self.state_data.get('last_session'):
            self.state_data['last_session']['uptime'] = uptime_seconds
            self.state_data['last_session']['status'] = 'completed'
        
        self._save_state()
        bot_logger.info(f"Сессия завершена. Время работы: {uptime_seconds:.1f}s")
    
    def record_failed_start(self, error_message: str = ""):
        """Записывает неудачный запуск"""
        self.state_data['statistics']['failed_starts'] += 1
        self.state_data['last_session'] = {
            'session_id': self.session_id,
            'start_time': self.current_session_start,
            'status': 'failed',
            'error': error_message[:200]  # Ограничиваем длину ошибки
        }
        self._save_state()
        bot_logger.error(f"Неудачный запуск записан: {error_message}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику бота"""
        current_uptime = time.time() - self.current_session_start
        
        return {
            'session_id': self.session_id,
            'current_uptime': current_uptime,
            'total_sessions': self.state_data['total_sessions'],
            'total_uptime': self.state_data['total_uptime'],
            'statistics': self.state_data['statistics'],
            'last_session': self.state_data.get('last_session')
        }
    
    def get_current_session_info(self) -> Dict[str, Any]:
        """Возвращает информацию о текущей сессии"""
        return {
            'session_id': self.session_id,
            'start_time': self.current_session_start,
            'uptime': time.time() - self.current_session_start,
            'status': 'running'
        }

# Создаем глобальный экземпляр менеджера состояния
bot_state_manager = BotStateManager()
