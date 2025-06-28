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
        self.processed_hours_file = "processed_hours.json"

        # Статистика Welford для онлайн расчета среднего и дисперсии
        self.count = 0
        self.mean = 0.0
        self.M2 = 0.0  # Сумма квадратов отклонений

        # Множество обработанных часов для избежания дублирования
        self.processed_hours = set()

        # Загружаем сохраненную статистику
        self._load_stats()
        self._load_processed_hours()

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

    def _load_processed_hours(self):
        """Загружает список обработанных часов"""
        if os.path.exists(self.processed_hours_file):
            try:
                with open(self.processed_hours_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.processed_hours = set(data.get('hours', []))

                bot_logger.debug(f"📊 Загружено {len(self.processed_hours)} обработанных часов")

            except Exception as e:
                bot_logger.warning(f"Ошибка загрузки обработанных часов: {e}")
                self.processed_hours = set()
        else:
            self.processed_hours = set()

    def _save_processed_hours(self):
        """Сохраняет список обработанных часов"""
        try:
            data = {
                'hours': list(self.processed_hours),
                'last_updated': datetime.now().isoformat()
            }

            with open(self.processed_hours_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            bot_logger.error(f"Ошибка сохранения обработанных часов: {e}")

    def _reset_stats(self):
        """Сброс статистики"""
        self.count = 0
        self.mean = 0.0
        self.M2 = 0.0
        self.processed_hours = set()

    def update_activity_stats(self, new_value: float, hour_key: str = None):
        """
        Обновляет статистику активности новым значением (алгоритм Welford)
        Учитывает все часы начиная с первого записанного, включая пустые часы как 0

        Args:
            new_value: Новое значение уровня активности в минутах
            hour_key: Уникальный ключ часа для избежания дублирования
        """
        # Если указан ключ часа, проверяем уникальность
        if hour_key:
            if hour_key in self.processed_hours:
                bot_logger.debug(f"📊 Час {hour_key} уже обработан, пропускаем")
                return

            # Если это первый час, просто добавляем его
            if not self.processed_hours:
                self.processed_hours.add(hour_key)
                self._save_processed_hours()

                self.count += 1
                delta = new_value - self.mean
                self.mean += delta / self.count
                delta2 = new_value - self.mean
                self.M2 += delta * delta2

                self._save_stats()
                bot_logger.info(f"📊 Первый час добавлен: час={hour_key}, значение={new_value:.1f}мин, среднее={self.mean:.1f}мин, count={self.count}")
                return

            # Находим пропущенные часы между последним записанным и текущим
            missing_hours = self._find_missing_hours(hour_key)

            # Добавляем пропущенные часы со значением 0
            for missing_hour in missing_hours:
                if missing_hour not in self.processed_hours:
                    bot_logger.debug(f"📊 Добавляем пропущенный час {missing_hour} со значением 0")
                    self.processed_hours.add(missing_hour)

                    # Обновляем статистику Welford для пропущенного часа (значение = 0)
                    self.count += 1
                    delta = 0.0 - self.mean
                    self.mean += delta / self.count
                    delta2 = 0.0 - self.mean
                    self.M2 += delta * delta2

            # Добавляем текущий час
            self.processed_hours.add(hour_key)
            self._save_processed_hours()

        # Обновляем статистику для текущего значения
        self.count += 1
        delta = new_value - self.mean
        self.mean += delta / self.count
        delta2 = new_value - self.mean
        self.M2 += delta * delta2

        # Сохраняем статистику
        self._save_stats()

        bot_logger.info(f"📊 Обновлена статистика активности: час={hour_key}, значение={new_value:.1f}мин, среднее={self.mean:.1f}мин, count={self.count}")

    def _find_missing_hours(self, current_hour_key: str) -> List[str]:
        """
        Находит пропущенные часы между последним записанным и текущим часом

        Args:
            current_hour_key: Текущий ключ часа в формате "YYYY-MM-DD_HH"

        Returns:
            Список ключей пропущенных часов
        """
        try:
            from datetime import datetime, timedelta

            if not self.processed_hours:
                return []

            # Парсим текущий час
            current_dt = datetime.strptime(current_hour_key, "%Y-%m-%d_%H")

            # Находим последний записанный час
            sorted_hours = sorted(self.processed_hours)
            last_hour_key = sorted_hours[-1]
            last_dt = datetime.strptime(last_hour_key, "%Y-%m-%d_%H")

            missing_hours = []

            # Генерируем все часы между последним и текущим
            current_check = last_dt + timedelta(hours=1)
            while current_check < current_dt:
                hour_key = current_check.strftime("%Y-%m-%d_%H")
                missing_hours.append(hour_key)
                current_check += timedelta(hours=1)

            return missing_hours

        except Exception as e:
            bot_logger.warning(f"Ошибка поиска пропущенных часов: {e}")
            return []

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
            hour_key = hour_dt.strftime('%H:00')

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
        # Важно: обрабатываем ВСЕ значения, включая нули!
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
        Простая сумма длительностей всех сессий в этом часу

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

    def get_stats_summary(self) -> Dict:
        """Возвращает сводку статистики"""
        # Если данных недостаточно, возвращаем None для средних значений
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


# Глобальный экземпляр
activity_calculator = ActivityLevelCalculator()