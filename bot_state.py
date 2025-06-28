
"""
Модуль управления состоянием MEXCScalping Assistant
"""

import json
import os
import time
from typing import Dict, Any, Optional
from logger import bot_logger

class BotStateManager:
    """Менеджер состояния бота"""
    
    def __init__(self, state_file: str = "bot_state.json"):
        self.state_file = state_file
        self.state: Dict[str, Any] = {}
        self.default_state = {
            "session_count": 0,
            "total_uptime": 0,
            "last_mode": None,
            "startup_count": 0,
            "last_startup": 0,
            "crash_count": 0,
            "successful_sessions": 0,
            "total_coins_monitored": 0,
            "total_alerts_sent": 0,
            "performance_history": [],
            "configuration_changes": [],
            "error_history": []
        }
        self.load()
    
    def load(self):
        """Загружает состояние из файла"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    file_state = json.load(f)
                    # Объединяем с дефолтными значениями
                    self.state = {**self.default_state, **file_state}
                    bot_logger.info("Состояние бота загружено")
            else:
                self.state = self.default_state.copy()
                self.save()
                bot_logger.info("Создано новое состояние бота")
        except Exception as e:
            bot_logger.error(f"Ошибка загрузки состояния: {e}")
            self.state = self.default_state.copy()
    
    def save(self):
        """Сохраняет состояние в файл"""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
            bot_logger.debug("Состояние бота сохранено")
        except Exception as e:
            bot_logger.error(f"Ошибка сохранения состояния: {e}")
    
    def increment_session(self):
        """Увеличивает счетчик сессий"""
        self.state['session_count'] += 1
        self.state['startup_count'] += 1
        self.state['last_startup'] = time.time()
        self.save()
        bot_logger.info(f"Новая сессия #{self.state['session_count']}")
    
    def add_uptime(self, uptime_seconds: float):
        """Добавляет время работы"""
        self.state['total_uptime'] += uptime_seconds
        self.save()
    
    def set_last_mode(self, mode: Optional[str]):
        """Устанавливает последний активный режим"""
        self.state['last_mode'] = mode
        self.save()
    
    def get_last_mode(self) -> Optional[str]:
        """Возвращает последний активный режим"""
        return self.state.get('last_mode')
    
    def record_crash(self):
        """Записывает факт сбоя"""
        self.state['crash_count'] += 1
        self.save()
        bot_logger.warning(f"Зафиксирован сбой #{self.state['crash_count']}")
    
    def record_successful_session(self):
        """Записывает успешную сессию"""
        self.state['successful_sessions'] += 1
        self.save()
    
    def add_coins_monitored(self, count: int):
        """Добавляет количество отслеживаемых монет"""
        self.state['total_coins_monitored'] += count
        self.save()
    
    def increment_alerts_sent(self):
        """Увеличивает счетчик отправленных алертов"""
        self.state['total_alerts_sent'] += 1
        self.save()
    
    def record_performance(self, performance_data: Dict[str, Any]):
        """Записывает данные о производительности"""
        performance_record = {
            'timestamp': time.time(),
            **performance_data
        }
        
        self.state['performance_history'].append(performance_record)
        
        # Ограничиваем размер истории
        if len(self.state['performance_history']) > 100:
            self.state['performance_history'] = self.state['performance_history'][-50:]
        
        self.save()
    
    def record_config_change(self, key: str, old_value: Any, new_value: Any):
        """Записывает изменение конфигурации"""
        change_record = {
            'timestamp': time.time(),
            'key': key,
            'old_value': old_value,
            'new_value': new_value
        }
        
        self.state['configuration_changes'].append(change_record)
        
        # Ограничиваем размер истории
        if len(self.state['configuration_changes']) > 50:
            self.state['configuration_changes'] = self.state['configuration_changes'][-25:]
        
        self.save()
        bot_logger.info(f"Изменение конфигурации: {key} = {new_value}")
    
    def record_error(self, error_type: str, error_message: str):
        """Записывает ошибку"""
        error_record = {
            'timestamp': time.time(),
            'type': error_type,
            'message': error_message[:500]  # Ограничиваем длину
        }
        
        self.state['error_history'].append(error_record)
        
        # Ограничиваем размер истории
        if len(self.state['error_history']) > 100:
            self.state['error_history'] = self.state['error_history'][-50:]
        
        self.save()
    
    def get_statistics(self) -> Dict[str, Any]:
        """Возвращает статистику работы бота"""
        current_time = time.time()
        
        # Рассчитываем среднее время работы сессии
        avg_uptime = 0
        if self.state['session_count'] > 0:
            avg_uptime = self.state['total_uptime'] / self.state['session_count']
        
        # Рассчитываем успешность сессий
        success_rate = 0
        if self.state['startup_count'] > 0:
            success_rate = (self.state['successful_sessions'] / self.state['startup_count']) * 100
        
        # Последние ошибки
        recent_errors = [e for e in self.state['error_history'] 
                        if current_time - e['timestamp'] < 3600]  # За последний час
        
        return {
            'session_count': self.state['session_count'],
            'total_uptime_hours': self.state['total_uptime'] / 3600,
            'avg_session_uptime_minutes': avg_uptime / 60,
            'startup_count': self.state['startup_count'],
            'crash_count': self.state['crash_count'],
            'success_rate_percent': success_rate,
            'total_coins_monitored': self.state['total_coins_monitored'],
            'total_alerts_sent': self.state['total_alerts_sent'],
            'last_startup': self.state['last_startup'],
            'recent_errors_count': len(recent_errors),
            'recent_config_changes': len([c for c in self.state['configuration_changes'] 
                                        if current_time - c['timestamp'] < 86400])  # За сутки
        }
    
    def get_health_indicators(self) -> Dict[str, Any]:
        """Возвращает индикаторы здоровья системы"""
        stats = self.get_statistics()
        current_time = time.time()
        
        # Определяем здоровье системы
        health_score = 100
        issues = []
        
        # Проверяем частоту сбоев
        if stats['crash_count'] > 5:
            health_score -= 20
            issues.append("Высокая частота сбоев")
        
        # Проверяем успешность сессий
        if stats['success_rate_percent'] < 80:
            health_score -= 15
            issues.append("Низкая успешность сессий")
        
        # Проверяем недавние ошибки
        if stats['recent_errors_count'] > 10:
            health_score -= 15
            issues.append("Много недавних ошибок")
        
        # Проверяем последний запуск
        time_since_startup = current_time - stats['last_startup']
        if time_since_startup > 86400:  # Больше суток
            health_score -= 10
            issues.append("Давно не запускался")
        
        # Определяем статус
        if health_score >= 90:
            status = "excellent"
        elif health_score >= 70:
            status = "good"
        elif health_score >= 50:
            status = "fair"
        else:
            status = "poor"
        
        return {
            'health_score': max(0, health_score),
            'status': status,
            'issues': issues,
            'uptime_stability': min(100, stats['avg_session_uptime_minutes'] * 2),  # Стабильность
            'error_rate': stats['recent_errors_count']
        }
    
    def cleanup_old_data(self):
        """Очищает старые данные"""
        current_time = time.time()
        
        # Удаляем старые данные производительности (старше 7 дней)
        self.state['performance_history'] = [
            p for p in self.state['performance_history']
            if current_time - p['timestamp'] < 604800
        ]
        
        # Удаляем старые ошибки (старше 24 часов)
        self.state['error_history'] = [
            e for e in self.state['error_history']
            if current_time - e['timestamp'] < 86400
        ]
        
        # Удаляем старые изменения конфигурации (старше 30 дней)
        self.state['configuration_changes'] = [
            c for c in self.state['configuration_changes']
            if current_time - c['timestamp'] < 2592000
        ]
        
        self.save()
        bot_logger.debug("Старые данные состояния очищены")

# Глобальный экземпляр менеджера состояния
bot_state_manager = BotStateManager()
