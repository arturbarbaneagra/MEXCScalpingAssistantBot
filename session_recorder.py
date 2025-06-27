
"""
Модуль записи сессий активных монет
Сохраняет данные о каждой активной сессии монеты в отдельные файлы по дням
"""

import os
import json
import time
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from logger import bot_logger


class SessionRecorder:
    def __init__(self):
        self.recording = False
        self.active_sessions: Dict[str, Dict] = {}
        self.data_dir = "session_data"
        self.current_date = None
        self.recording_task = None
        
        # Создаем директорию для данных
        os.makedirs(self.data_dir, exist_ok=True)
        
        bot_logger.info("📝 Session Recorder инициализирован")

    def start_recording(self):
        """Запускает запись сессий"""
        if self.recording:
            bot_logger.warning("Session Recorder уже работает")
            return
            
        self.recording = True
        self.recording_task = asyncio.create_task(self._recording_loop())
        bot_logger.info("🎬 Session Recorder запущен")

    def stop_recording(self):
        """Останавливает запись сессий"""
        if not self.recording:
            return
            
        self.recording = False
        
        if self.recording_task and not self.recording_task.done():
            self.recording_task.cancel()
            
        # Сохраняем все активные сессии
        self._save_all_sessions()
        
        bot_logger.info("⏹️ Session Recorder остановлен")

    def update_coin_activity(self, symbol: str, coin_data: Dict[str, Any]):
        """Обновляет данные активности монеты"""
        if not self.recording or not coin_data.get('active'):
            return
            
        current_time = time.time()
        current_minute = int(current_time // 60) * 60  # Округляем до минуты
        
        # Если монета впервые стала активной
        if symbol not in self.active_sessions:
            self.active_sessions[symbol] = {
                'symbol': symbol,
                'start_time': current_time,
                'start_datetime': datetime.fromtimestamp(current_time).isoformat(),
                'last_update': current_time,
                'minutes_data': {},
                'total_duration': 0
            }
            bot_logger.debug(f"📊 Начата новая сессия для {symbol}")
        
        session = self.active_sessions[symbol]
        session['last_update'] = current_time
        
        # Сохраняем данные за текущую минуту
        minute_key = str(current_minute)
        if minute_key not in session['minutes_data']:
            session['minutes_data'][minute_key] = {
                'timestamp': current_minute,
                'datetime': datetime.fromtimestamp(current_minute).isoformat(),
                'trades': 0,
                'volume': 0.0,
                'price': 0.0,
                'change': 0.0,
                'spread': 0.0,
                'natr': 0.0
            }
        
        # Обновляем данные минуты
        minute_data = session['minutes_data'][minute_key]
        minute_data['trades'] = coin_data.get('trades', 0)
        minute_data['volume'] = coin_data.get('volume', 0.0)
        minute_data['price'] = coin_data.get('price', 0.0)
        minute_data['change'] = coin_data.get('change', 0.0)
        minute_data['spread'] = coin_data.get('spread', 0.0)
        minute_data['natr'] = coin_data.get('natr', 0.0)

    def check_inactive_sessions(self, active_coins: Dict[str, Any]):
        """Проверяет и завершает неактивные сессии"""
        if not self.recording:
            return
            
        current_time = time.time()
        inactive_threshold = 90  # 1.5 минуты без активности
        
        sessions_to_complete = []
        
        for symbol in list(self.active_sessions.keys()):
            session = self.active_sessions[symbol]
            time_since_update = current_time - session['last_update']
            
            # Проверяем если монета больше не активна или давно не обновлялась
            is_still_active = symbol in active_coins and active_coins[symbol].get('active', False)
            
            if not is_still_active or time_since_update > inactive_threshold:
                # Проверяем минимальную длительность сессии (1 минута)
                session_duration = current_time - session['start_time']
                if session_duration >= 60:  # Минимум 1 минута
                    sessions_to_complete.append(symbol)
                else:
                    # Удаляем слишком короткие сессии
                    del self.active_sessions[symbol]
                    bot_logger.debug(f"🗑️ Удалена короткая сессия {symbol} ({session_duration:.1f}s)")
        
        # Завершаем длинные сессии
        for symbol in sessions_to_complete:
            self._complete_session(symbol)

    def _complete_session(self, symbol: str):
        """Завершает и сохраняет сессию монеты"""
        if symbol not in self.active_sessions:
            return
            
        session = self.active_sessions[symbol]
        current_time = time.time()
        
        # Завершаем сессию
        session['end_time'] = current_time
        session['end_datetime'] = datetime.fromtimestamp(current_time).isoformat()
        session['total_duration'] = current_time - session['start_time']
        session['total_minutes'] = len(session['minutes_data'])
        
        # Подсчитываем статистику
        if session['minutes_data']:
            total_trades = sum(data['trades'] for data in session['minutes_data'].values())
            total_volume = sum(data['volume'] for data in session['minutes_data'].values())
            avg_price = sum(data['price'] for data in session['minutes_data'].values()) / len(session['minutes_data'])
            
            session['summary'] = {
                'total_trades': total_trades,
                'total_volume': total_volume,
                'avg_price': avg_price,
                'max_trades_per_minute': max(data['trades'] for data in session['minutes_data'].values()),
                'max_volume_per_minute': max(data['volume'] for data in session['minutes_data'].values())
            }
        else:
            session['summary'] = {
                'total_trades': 0,
                'total_volume': 0.0,
                'avg_price': 0.0,
                'max_trades_per_minute': 0,
                'max_volume_per_minute': 0.0
            }
        
        # Сохраняем сессию в файл
        self._save_session(session)
        
        # Удаляем из активных
        del self.active_sessions[symbol]
        
        bot_logger.info(
            f"✅ Сессия {symbol} завершена: {session['total_duration']:.1f}s, "
            f"{session['total_minutes']} минут, {session['summary']['total_trades']} сделок"
        )

    def _save_session(self, session: Dict):
        """Сохраняет сессию в файл"""
        try:
            # Определяем дату начала сессии
            start_date = datetime.fromtimestamp(session['start_time']).strftime('%Y-%m-%d')
            filename = f"sessions_{start_date}.json"
            filepath = os.path.join(self.data_dir, filename)
            
            # Загружаем существующие данные или создаем новые
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    daily_data = json.load(f)
            else:
                daily_data = {
                    'date': start_date,
                    'sessions': [],
                    'metadata': {
                        'created': datetime.now().isoformat(),
                        'total_sessions': 0,
                        'total_duration': 0
                    }
                }
            
            # Добавляем новую сессию
            daily_data['sessions'].append(session)
            daily_data['metadata']['total_sessions'] = len(daily_data['sessions'])
            daily_data['metadata']['total_duration'] = sum(s['total_duration'] for s in daily_data['sessions'])
            daily_data['metadata']['last_updated'] = datetime.now().isoformat()
            
            # Сохраняем
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(daily_data, f, indent=2, ensure_ascii=False)
                
            bot_logger.debug(f"💾 Сессия {session['symbol']} сохранена в {filename}")
            
        except Exception as e:
            bot_logger.error(f"Ошибка сохранения сессии {session['symbol']}: {e}")

    def _save_all_sessions(self):
        """Сохраняет все текущие активные сессии"""
        for symbol in list(self.active_sessions.keys()):
            self._complete_session(symbol)

    async def _recording_loop(self):
        """Основной цикл записи"""
        try:
            while self.recording:
                try:
                    # Проверяем неактивные сессии каждые 30 секунд
                    from telegram_bot import telegram_bot
                    if hasattr(telegram_bot, 'active_coins'):
                        self.check_inactive_sessions(telegram_bot.active_coins)
                    
                    # Очистка старых данных раз в час
                    if int(time.time()) % 3600 < 30:
                        self._cleanup_old_data()
                    
                    await asyncio.sleep(30)
                    
                except Exception as e:
                    bot_logger.error(f"Ошибка в цикле записи сессий: {e}")
                    await asyncio.sleep(5)
                    
        except asyncio.CancelledError:
            bot_logger.info("🔄 Цикл записи сессий отменен")
        except Exception as e:
            bot_logger.error(f"Критическая ошибка в цикле записи: {e}")

    def _cleanup_old_data(self):
        """Очистка старых файлов данных (старше 30 дней)"""
        try:
            cutoff_date = datetime.now() - timedelta(days=30)
            cutoff_str = cutoff_date.strftime('%Y-%m-%d')
            
            for filename in os.listdir(self.data_dir):
                if filename.startswith('sessions_') and filename.endswith('.json'):
                    file_date = filename.replace('sessions_', '').replace('.json', '')
                    if file_date < cutoff_str:
                        filepath = os.path.join(self.data_dir, filename)
                        os.remove(filepath)
                        bot_logger.info(f"🗑️ Удален старый файл сессий: {filename}")
                        
        except Exception as e:
            bot_logger.error(f"Ошибка очистки старых данных: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику записи"""
        return {
            'recording': self.recording,
            'active_sessions': len(self.active_sessions),
            'data_directory': self.data_dir,
            'session_symbols': list(self.active_sessions.keys())
        }

    def get_daily_summary(self, date: str = None) -> Optional[Dict]:
        """Возвращает сводку за день"""
        if not date:
            date = datetime.now().strftime('%Y-%m-%d')
            
        filename = f"sessions_{date}.json"
        filepath = os.path.join(self.data_dir, filename)
        
        try:
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return None
        except Exception as e:
            bot_logger.error(f"Ошибка чтения данных за {date}: {e}")
            return None


# Глобальный экземпляр
session_recorder = SessionRecorder()
