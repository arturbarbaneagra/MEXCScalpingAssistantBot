"""
Модуль записи сессий активных монет
Сохраняет данные о каждой активной сессии монеты в отдельные файлы по дням
"""

import os
import json
import time
import asyncio
from datetime import datetime
from typing import Dict, Optional, Any
from logger import bot_logger
from config import config_manager


class SessionRecorder:
    def __init__(self):
        self.recording = False
        self.active_sessions: Dict[str, Dict] = {}
        self.data_directory = "session_data"
        self.daily_files: Dict[str, str] = {}  # date -> filepath
        self.session_start_threshold = 60  # Минимум 60 секунд для записи сессии

        # Создаем директорию если не существует
        if not os.path.exists(self.data_directory):
            os.makedirs(self.data_directory)
            bot_logger.info(f"📁 Создана директория сессий: {self.data_directory}")

    def start_recording(self):
        """Запуск записи сессий"""
        self.recording = True
        bot_logger.info("📝 Session Recorder запущен")

    def stop_recording(self):
        """Остановка записи сессий"""
        self.recording = False

        # Завершаем все активные сессии
        for symbol in list(self.active_sessions.keys()):
            self._finalize_session(symbol, force=True)

        bot_logger.info("📝 Session Recorder остановлен")

    def update_coin_activity(self, symbol: str, coin_data: Dict):
        """Обновляет активность монеты"""
        if not self.recording:
            return

        current_time = time.time()
        is_active = coin_data.get('active', False)

        if is_active:
            if symbol not in self.active_sessions:
                # Начинаем новую сессию
                self.active_sessions[symbol] = {
                    'symbol': symbol,
                    'start_time': current_time,
                    'last_update': current_time,
                    'data_points': [],
                    'total_minutes': 0,
                    'summary': {
                        'max_volume': 0,
                        'total_volume': 0,
                        'total_trades': 0,
                        'max_change': 0,
                        'min_change': 0,
                        'max_natr': 0,
                        'max_spread': 0,
                        'avg_price': 0,
                        'price_samples': []
                    }
                }
                bot_logger.debug(f"📝 Начата запись сессии для {symbol}")

            # Обновляем сессию
            session = self.active_sessions[symbol]
            session['last_update'] = current_time

            # Добавляем точку данных
            data_point = {
                'timestamp': current_time,
                'volume': coin_data.get('volume', 0),
                'trades': coin_data.get('trades', 0),
                'change': coin_data.get('change', 0),
                'natr': coin_data.get('natr', 0),
                'spread': coin_data.get('spread', 0),
                'price': coin_data.get('price', 0)
            }
            session['data_points'].append(data_point)

            # Обновляем сводку
            summary = session['summary']
            volume = coin_data.get('volume', 0)
            trades = coin_data.get('trades', 0)
            change = coin_data.get('change', 0)
            natr = coin_data.get('natr', 0)
            spread = coin_data.get('spread', 0)
            price = coin_data.get('price', 0)

            summary['max_volume'] = max(summary['max_volume'], volume)
            summary['total_volume'] += volume
            summary['total_trades'] += trades
            summary['max_change'] = max(summary['max_change'], change)
            summary['min_change'] = min(summary['min_change'], change)
            summary['max_natr'] = max(summary['max_natr'], natr)
            summary['max_spread'] = max(summary['max_spread'], spread)

            if price > 0:
                summary['price_samples'].append(price)
                summary['avg_price'] = sum(summary['price_samples']) / len(summary['price_samples'])

            # Обновляем общее время в минутах
            session['total_minutes'] = len(session['data_points'])

            bot_logger.debug(f"📊 Обновлена сессия {symbol}: {session['total_minutes']} точек данных")

    def check_inactive_sessions(self, active_coins: Dict):
        """Проверяет неактивные сессии и завершает их"""
        if not self.recording:
            return

        current_time = time.time()
        inactive_threshold = config_manager.get('INACTIVITY_TIMEOUT', 90)  # 90 секунд

        sessions_to_finalize = []

        for symbol, session in list(self.active_sessions.items()):
            # Проверяем, активна ли монета в активных списках
            coin_still_active = symbol in active_coins

            # Проверяем время последнего обновления
            time_since_update = current_time - session['last_update']

            # Завершаем сессию если монета неактивна или давно не обновлялась
            if not coin_still_active or time_since_update > inactive_threshold:
                sessions_to_finalize.append(symbol)

        # Завершаем неактивные сессии
        for symbol in sessions_to_finalize:
            self._finalize_session(symbol)

    def _finalize_session(self, symbol: str, force: bool = False):
        """Завершает и сохраняет сессию"""
        if symbol not in self.active_sessions:
            return

        session = self.active_sessions[symbol]
        current_time = time.time()

        # Рассчитываем длительность
        duration = current_time - session['start_time']
        session['end_time'] = current_time
        session['total_duration'] = duration

        # Добавляем временные метки
        session['start_datetime'] = datetime.fromtimestamp(session['start_time']).isoformat()
        session['end_datetime'] = datetime.fromtimestamp(current_time).isoformat()

        # Сохраняем только если сессия была достаточно долгой
        if duration >= self.session_start_threshold or force:
            self._save_session_to_file(session)

            duration_min = int(duration // 60)
            duration_sec = int(duration % 60)
            bot_logger.info(
                f"💾 Сессия {symbol} сохранена: {duration_min}м {duration_sec}с, "
                f"{session['total_minutes']} точек данных, "
                f"макс.объем ${session['summary']['max_volume']:,.0f}"
            )
        else:
            bot_logger.debug(f"⏭ Сессия {symbol} слишком короткая ({duration:.1f}с), не сохраняем")

        # Удаляем из активных сессий
        del self.active_sessions[symbol]

    def _save_session_to_file(self, session: Dict):
        """Сохраняет сессию в файл"""
        try:
            # Определяем дату для файла
            date_str = datetime.fromtimestamp(session['start_time']).strftime('%Y-%m-%d')
            filepath = os.path.join(self.data_directory, f"sessions_{date_str}.json")

            # Загружаем существующие данные или создаем новый файл
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        daily_data = json.load(f)
                except Exception as e:
                    bot_logger.warning(f"Ошибка чтения {filepath}: {e}, создаем новый")
                    daily_data = {'date': date_str, 'sessions': [], 'metadata': {}}
            else:
                daily_data = {'date': date_str, 'sessions': [], 'metadata': {}}

            # Добавляем новую сессию
            daily_data['sessions'].append(session)

            # Обновляем метаданные
            total_sessions = len(daily_data['sessions'])
            total_duration = sum(s.get('total_duration', 0) for s in daily_data['sessions'])
            total_volume = sum(s.get('summary', {}).get('total_volume', 0) for s in daily_data['sessions'])

            daily_data['metadata'] = {
                'total_sessions': total_sessions,
                'total_duration': total_duration,
                'total_volume': total_volume,
                'last_updated': datetime.now().isoformat(),
                'unique_symbols': len(set(s.get('symbol') for s in daily_data['sessions']))
            }

            # Сохраняем в файл
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(daily_data, f, indent=2, ensure_ascii=False)

            bot_logger.debug(f"💾 Сессия сохранена в {filepath}")

        except Exception as e:
            bot_logger.error(f"Ошибка сохранения сессии {session['symbol']}: {e}")

    def get_daily_summary(self, date_str: str) -> Optional[Dict]:
        """Возвращает сводку за определенный день"""
        filepath = os.path.join(self.data_directory, f"sessions_{date_str}.json")

        if not os.path.exists(filepath):
            return None

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            bot_logger.error(f"Ошибка чтения файла {filepath}: {e}")
            return None

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику Session Recorder"""
        return {
            'recording': self.recording,
            'active_sessions': len(self.active_sessions),
            'session_symbols': list(self.active_sessions.keys()),
            'data_directory': self.data_directory
        }


# Глобальный экземпляр
session_recorder = SessionRecorder()