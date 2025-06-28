"""
Модуль для расчета уровня активности по часам с использованием статистики Welford
"""

import os
import json
import math
from typing import Dict, List
from datetime import datetime, timedelta
from logger import bot_logger


class ActivityLevelCalculator:
    def __init__(self):
        self.stats_file = "activity_stats.json"

        # Статистика Welford - только три переменные
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

                std = self.get_std_dev()
                bot_logger.info(f"📊 Загружена статистика активности: count={self.count}, mean={self.mean:.2f}, std={std:.2f}")

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

    def update_with_new_value(self, new_value: float):
        """
        Обновляет статистику новым значением по алгоритму Welford

        Args:
            new_value: Новое значение уровня активности в минутах
        """
        # Алгоритм Welford для онлайн обновления
        self.count += 1
        delta = new_value - self.mean
        self.mean += delta / self.count
        delta2 = new_value - self.mean
        self.M2 += delta * delta2

        # Сохраняем обновленную статистику
        self._save_stats()

        std = self.get_std_dev()
        bot_logger.info(f"📊 Обновлена статистика: значение={new_value:.1f}мин, среднее={self.mean:.2f}мин, std={std:.2f}, count={self.count}")

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

    def get_last_24_hours_activity(self) -> List[float]:
        """Получает активность за последние 24 часа с заполнением нулями"""
        from datetime import datetime, timedelta
        import os
        import json

        now_moscow = datetime.now() + timedelta(hours=3)
        activities = []

        for i in range(24):
            hour_dt = now_moscow - timedelta(hours=i)

            # Попробуем найти данные в файлах сессий
            date_str = hour_dt.strftime('%Y-%m-%d')
            activity_found = False

            # Проверяем файл за эту дату
            filepath = os.path.join("session_data", f"sessions_{date_str}.json")
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        daily_data = json.load(f)

                    # Ищем сессии в этом часу
                    hour_sessions = []
                    cutoff_start = hour_dt.replace(minute=0, second=0, microsecond=0).timestamp()
                    cutoff_end = cutoff_start + 3600  # +1 час

                    for session in daily_data.get('sessions', []):
                        start_time = session.get('start_time', 0)
                        if cutoff_start <= start_time < cutoff_end:
                            hour_sessions.append(session)

                    if hour_sessions:
                        total_activity = sum(s.get('total_duration', 0) / 60 for s in hour_sessions)
                        activities.append(total_activity)
                        activity_found = True

                except Exception as e:
                    bot_logger.debug(f"Ошибка чтения файла {filepath}: {e}")

            # Если не нашли данные для этого часа, добавляем 0.0
            if not activity_found:
                activities.append(0.0)

        return activities

    def calculate_activity_statistics_welford(self, activities: List[float]) -> Dict[str, float]:
        """Рассчитывает статистику активности по алгоритму Welford для ВСЕХ 24 часов"""
        if not activities:
            return {'mean': 0.0, 'std': 0.0, 'count': 0}

        # Алгоритм Welford для онлайн расчета среднего и дисперсии
        count = 0
        mean = 0.0
        M2 = 0.0  # сумма квадратов отклонений

        for activity in activities:
            count += 1
            delta = activity - mean
            mean += delta / count
            delta2 = activity - mean
            M2 += delta * delta2

        if count < 2:
            variance = 0.0
        else:
            variance = M2 / (count - 1)  # выборочная дисперсия

        std = variance ** 0.5

        return {
            'mean': mean,
            'std': std,
            'count': count,
            'variance': variance
        }

    def get_activity_level_info(self, total_activity_minutes: float) -> Dict:
        """
        Возвращает информацию об уровне активности

        Args:
            total_activity_minutes: Общее время активности в минутах

        Returns:
            Словарь с информацией об уровне активности
        """
        z_score = self.get_z_score(total_activity_minutes)

        # Если статистики еще недостаточно, используем простые пороги
        if self.count < 5:
            # Простая классификация по абсолютным значениям
            if total_activity_minutes >= 20:
                level = "Экстремально высокая"
                emoji = "🔥🔥🔥"
                color = "🟥"  # Красный
            elif total_activity_minutes >= 15:
                level = "Очень высокая"
                emoji = "🔥🔥"
                color = "🟧"  # Оранжевый
            elif total_activity_minutes >= 10:
                level = "Высокая"
                emoji = "🔥"
                color = "🟨"  # Желтый
            elif total_activity_minutes >= 7:
                level = "Выше средней"
                emoji = "📈"
                color = "🟩"  # Зеленый
            elif total_activity_minutes >= 4:
                level = "Средняя"
                emoji = "📊"
                color = "🟦"  # Синий
            elif total_activity_minutes >= 2:
                level = "Ниже средней"
                emoji = "📉"
                color = "🟪"  # Фиолетовый
            elif total_activity_minutes >= 1:
                level = "Низкая"
                emoji = "❄️"
                color = "⬜"  # Белый
            else:
                level = "Очень низкая"
                emoji = "💤"
                color = "⬛"  # Черный
        else:
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
        total_activity = 0.0

        for session in sessions:
            duration = session.get('total_duration', 0) / 60  # В минутах
            total_activity += duration

        return total_activity

    def update_activity_stats(self, activity_value: float, hour_key: str = None):
        """
        Обновляет статистику активности новым значением
        
        Args:
            activity_value: Значение активности в минутах
            hour_key: Ключ часа (не используется в алгоритме Welford)
        """
        self.update_with_new_value(activity_value)

    def get_stats_summary(self) -> Dict:
        """Возвращает сводку статистики"""
        if self.count < 2:
            return {
                'count': self.count,
                'mean': self.mean if self.count > 0 else 0.0,
                'std_dev': 0.0,
                'variance': 0.0
            }

        return {
            'count': self.count,
            'mean': self.mean,
            'std_dev': self.get_std_dev(),
            'variance': self.get_variance()
        }

    def generate_24h_activity_report(self) -> str:
        """
        Генерирует отчет активности за последние 24 часа
        
        Returns:
            Отформатированный отчет активности
        """
        try:
            # Получаем данные активности за 24 часа
            activities = self.get_last_24_hours_activity()
            
            if not activities:
                return "❌ Нет данных об активности за последние 24 часа"
            
            # Рассчитываем статистику
            stats = self.calculate_activity_statistics_welford(activities)
            total_activity = sum(activities)
            
            # Получаем информацию об уровне активности
            activity_info = self.get_activity_level_info(total_activity)
            
            # Находим часы с максимальной активностью
            max_activity = max(activities)
            max_hour_index = activities.index(max_activity)
            
            # Считаем активные часы (с активностью > 0)
            active_hours = sum(1 for a in activities if a > 0)
            
            # Форматируем отчет
            report_lines = []
            
            # Заголовок с общей информацией
            report_lines.append(f"{activity_info['color']} <b>Уровень активности: {activity_info['level']}</b> {activity_info['emoji']}")
            report_lines.append("")
            
            # Основная статистика
            report_lines.append("<b>📊 Статистика за 24 часа:</b>")
            report_lines.append(f"• Общая активность: <b>{total_activity:.1f} минут</b>")
            report_lines.append(f"• Активных часов: <b>{active_hours}/24</b>")
            report_lines.append(f"• Максимум за час: <b>{max_activity:.1f} мин</b> ({max_hour_index} часов назад)")
            report_lines.append(f"• Среднее за час: <b>{stats['mean']:.1f} мин</b>")
            
            if stats['std'] > 0:
                report_lines.append(f"• Z-score: <b>{activity_info['z_score']:.2f}</b>")
            
            report_lines.append("")
            
            # Топ-5 самых активных часов
            indexed_activities = [(i, act) for i, act in enumerate(activities) if act > 0]
            indexed_activities.sort(key=lambda x: x[1], reverse=True)
            
            if indexed_activities:
                report_lines.append("<b>🔥 Топ активных часов:</b>")
                for i, (hour_idx, activity) in enumerate(indexed_activities[:5]):
                    hours_ago = hour_idx
                    if hours_ago == 0:
                        time_label = "текущий час"
                    elif hours_ago == 1:
                        time_label = "1 час назад"
                    else:
                        time_label = f"{hours_ago} часов назад"
                    
                    report_lines.append(f"• <b>{activity:.1f} мин</b> - {time_label}")
                
                report_lines.append("")
            
            # Визуализация последних 12 часов
            report_lines.append("<b>📈 Последние 12 часов:</b>")
            visual_line = ""
            for i in range(12):
                activity = activities[i]
                if activity >= 10:
                    visual_line += "🔥"
                elif activity >= 5:
                    visual_line += "🔴"
                elif activity >= 2:
                    visual_line += "🟡"
                elif activity >= 1:
                    visual_line += "🟢"
                else:
                    visual_line += "⚪"
            
            report_lines.append(f"<code>{visual_line}</code>")
            report_lines.append("<i>🔥≥10мин 🔴≥5мин 🟡≥2мин 🟢≥1мин ⚪<1мин</i>")
            
            # Информация о статистической модели
            if self.count >= 5:
                report_lines.append("")
                report_lines.append(f"<i>📊 Статистика основана на {self.count} наблюдениях</i>")
            else:
                report_lines.append("")
                report_lines.append(f"<i>⚠️ Мало данных для статистики ({self.count} наблюдений)</i>")
            
            return "\n".join(report_lines)
            
        except Exception as e:
            bot_logger.error(f"Ошибка генерации отчета активности: {e}")
            return f"❌ Ошибка генерации отчета: {str(e)}"


# Глобальный экземпляр
activity_calculator = ActivityLevelCalculator()