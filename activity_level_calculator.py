
"""
Модуль для расчета уровня активности по часам с использованием статистики Welford
"""

import os
import json
import time
import math
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
from logger import bot_logger


class ActivityLevelCalculator:
    def __init__(self):
        self.stats_file = "activity_stats.json"
        
        # Статистика Welford для онлайн расчета среднего и дисперсии
        self.count = 0
        self.mean = 0.0
        self.M2 = 0.0  # Сумма квадратов отклонений
        
        # Загружаем сохраненную статистику
        self._load_stats()
    
    def _load_stats(self):
        """Загружает сохраненную статистику"""
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                self.count = data.get('count', 0)
                self.mean = data.get('mean', 0.0)
                self.M2 = data.get('M2', 0.0)
                
                bot_logger.info(f"📊 Загружена статистика активности: count={self.count}, mean={self.mean:.1f}")
                
            except Exception as e:
                bot_logger.warning(f"Ошибка загрузки статистики активности: {e}")
                self._reset_stats()
        else:
            self._reset_stats()
    
    def _save_stats(self):
        """Сохраняет текущую статистику"""
        try:
            data = {
                'count': self.count,
                'mean': self.mean,
                'M2': self.M2,
                'last_updated': datetime.now().isoformat()
            }
            
            with open(self.stats_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            bot_logger.error(f"Ошибка сохранения статистики активности: {e}")
    
    def _reset_stats(self):
        """Сброс статистики"""
        self.count = 0
        self.mean = 0.0
        self.M2 = 0.0
    
    def update_activity_stats(self, new_value: float):
        """
        Обновляет статистику активности новым значением (алгоритм Welford)
        
        Args:
            new_value: Новое значение уровня активности в минутах
        """
        self.count += 1
        delta = new_value - self.mean
        self.mean += delta / self.count
        delta2 = new_value - self.mean
        self.M2 += delta * delta2
        
        # Сохраняем каждые 10 обновлений
        if self.count % 10 == 0:
            self._save_stats()
    
    def get_variance(self) -> float:
        """Возвращает дисперсию"""
        if self.count < 2:
            return 0.0
        return self.M2 / (self.count - 1)
    
    def get_std_dev(self) -> float:
        """Возвращает стандартное отклонение"""
        return math.sqrt(self.get_variance())
    
    def get_z_score(self, value: float) -> float:
        """
        Возвращает z-score для значения
        
        Args:
            value: Значение для которого нужен z-score
            
        Returns:
            Z-score (количество стандартных отклонений от среднего)
        """
        if self.count < 2:
            return 0.0
        
        std_dev = self.get_std_dev()
        if std_dev == 0:
            return 0.0
        
        return (value - self.mean) / std_dev
    
    def get_activity_level_info(self, total_activity_minutes: float) -> Dict:
        """
        Возвращает информацию об уровне активности
        
        Args:
            total_activity_minutes: Общее время активности в минутах
            
        Returns:
            Словарь с информацией об уровне активности
        """
        z_score = self.get_z_score(total_activity_minutes)
        
        # Определяем уровень активности и эмодзи на основе z-score
        if z_score >= 2.0:
            level = "Экстремально высокая"
            emoji = "🔥🔥🔥"
            color = "🟥"  # Красный
        elif z_score >= 1.5:
            level = "Очень высокая"
            emoji = "🔥🔥"
            color = "🟧"  # Оранжевый
        elif z_score >= 1.0:
            level = "Высокая"
            emoji = "🔥"
            color = "🟨"  # Желтый
        elif z_score >= 0.5:
            level = "Выше средней"
            emoji = "📈"
            color = "🟩"  # Зеленый
        elif z_score >= -0.5:
            level = "Средняя"
            emoji = "📊"
            color = "🟦"  # Синий
        elif z_score >= -1.0:
            level = "Ниже средней"
            emoji = "📉"
            color = "🟪"  # Фиолетовый
        elif z_score >= -1.5:
            level = "Низкая"
            emoji = "❄️"
            color = "⬜"  # Белый
        else:
            level = "Очень низкая"
            emoji = "💤"
            color = "⬛"  # Черный
        
        return {
            'level': level,
            'emoji': emoji,
            'color': color,
            'z_score': z_score,
            'value': total_activity_minutes,
            'mean': self.mean,
            'std_dev': self.get_std_dev(),
            'count': self.count
        }
    
    def calculate_hourly_activity(self, sessions: List[Dict], hour_start: datetime) -> float:
        """
        Рассчитывает общее время активности для определенного часа
        
        Args:
            sessions: Список сессий
            hour_start: Начало часа для расчета
            
        Returns:
            Общее время активности в минутах
        """
        hour_end = hour_start + timedelta(hours=1)
        hour_start_ts = hour_start.timestamp()
        hour_end_ts = hour_end.timestamp()
        
        total_activity = 0.0
        
        for session in sessions:
            session_start = session.get('start_time', 0)
            session_end = session.get('end_time', 0)
            
            # Проверяем пересечение сессии с часом
            overlap_start = max(session_start, hour_start_ts)
            overlap_end = min(session_end, hour_end_ts)
            
            if overlap_start < overlap_end:
                overlap_duration = (overlap_end - overlap_start) / 60  # В минутах
                total_activity += overlap_duration
        
        return total_activity
    
    def get_stats_summary(self) -> Dict:
        """Возвращает сводку статистики"""
        return {
            'count': self.count,
            'mean': self.mean,
            'std_dev': self.get_std_dev(),
            'variance': self.get_variance()
        }


# Глобальный экземпляр
activity_calculator = ActivityLevelCalculator()
