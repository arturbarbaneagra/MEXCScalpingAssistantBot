
"""
Индивидуальный рекордер сессий для каждого пользователя
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
from logger import bot_logger


class UserSessionRecorder:
    def __init__(self, chat_id: str):
        self.chat_id = str(chat_id)
        self.data_directory = f"user_sessions_{self.chat_id}"
        self.active_sessions: Dict[str, Dict] = {}
        self.recording = False
        self.session_start_threshold = 60

        # Создаем директорию пользователя
        if not os.path.exists(self.data_directory):
            os.makedirs(self.data_directory)
            bot_logger.info(f"📁 Создана директория сессий для пользователя {self.chat_id}")

    def start_recording(self):
        """Запуск записи сессий"""
        self.recording = True
        bot_logger.info(f"📝 Session Recorder запущен для пользователя {self.chat_id}")

    def stop_recording(self):
        """Остановка записи сессий"""
        self.recording = False
        # Завершаем все активные сессии
        for symbol in list(self.active_sessions.keys()):
            self._finalize_session(symbol, force=True)
        bot_logger.info(f"📝 Session Recorder остановлен для пользователя {self.chat_id}")

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
                bot_logger.info(f"📝 Начата запись сессии {symbol} для пользователя {self.chat_id} (объем: ${coin_data.get('volume', 0):,.0f})")

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

            session['total_minutes'] = len(session['data_points'])

    def check_inactive_sessions(self, active_coins: Dict):
        """Проверяет неактивные сессии и завершает их"""
        if not self.recording:
            return

        current_time = time.time()
        inactive_threshold = 90

        sessions_to_finalize = []

        for symbol, session in list(self.active_sessions.items()):
            coin_still_active = symbol in active_coins
            time_since_update = current_time - session['last_update']

            if not coin_still_active or time_since_update > inactive_threshold:
                sessions_to_finalize.append(symbol)

        for symbol in sessions_to_finalize:
            self._finalize_session(symbol)

    def _finalize_session(self, symbol: str, force: bool = False):
        """Завершает и сохраняет сессию"""
        if symbol not in self.active_sessions:
            return

        session = self.active_sessions[symbol]
        current_time = time.time()

        duration = current_time - session['start_time']
        session['end_time'] = current_time
        session['total_duration'] = duration

        session['start_datetime'] = datetime.fromtimestamp(session['start_time']).isoformat()
        session['end_datetime'] = datetime.fromtimestamp(current_time).isoformat()

        if duration >= self.session_start_threshold or force:
            self._save_session_to_file(session)
            bot_logger.info(f"📝 Сессия {symbol} сохранена для пользователя {self.chat_id} (длительность: {duration:.1f}с)")
        else:
            bot_logger.debug(f"📝 Сессия {symbol} для пользователя {self.chat_id} пропущена (слишком короткая: {duration:.1f}с)")

        del self.active_sessions[symbol]

    def _save_session_to_file(self, session: Dict):
        """Сохраняет сессию в файл"""
        try:
            date_str = datetime.fromtimestamp(session['start_time']).strftime('%Y-%m-%d')
            filepath = os.path.join(self.data_directory, f"sessions_{date_str}.json")

            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        daily_data = json.load(f)
                except Exception:
                    daily_data = {'date': date_str, 'sessions': [], 'metadata': {}}
            else:
                daily_data = {'date': date_str, 'sessions': [], 'metadata': {}}

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

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(daily_data, f, indent=2, ensure_ascii=False)

        except Exception as e:
            bot_logger.error(f"Ошибка сохранения сессии {session['symbol']} для пользователя {self.chat_id}: {e}")

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
            'data_directory': self.data_directory,
            'chat_id': self.chat_id
        }
