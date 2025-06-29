
"""
Модуль записи сессий активных монет
Сохраняет данные о каждой активной сессии монеты в отдельные файлы по дням
Полностью автономный с максимальной защитой от сбоев
"""

import os
import json
import time
import asyncio
import threading
from datetime import datetime
from typing import Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor
import traceback
import sys


class AutonomousSessionRecorder:
    def __init__(self):
        self.recording = False
        self.active_sessions: Dict[str, Dict] = {}
        self.data_directory = "session_data"
        self.daily_files: Dict[str, str] = {}
        self.session_start_threshold = 60  # Минимум 60 секунд для записи сессии
        
        # Автономные настройки
        self.auto_save_interval = 30  # Автосохранение каждые 30 секунд
        self.emergency_save_interval = 10  # Экстренное сохранение каждые 10 секунд
        self.max_session_memory = 1000  # Максимум сессий в памяти
        self.fallback_logger = self._create_fallback_logger()
        
        # Защита от сбоев
        self.error_count = 0
        self.max_errors = 50
        self.last_emergency_save = 0
        self.emergency_mode = False
        
        # Внутренний executor для async операций
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="SessionRecorder")
        
        # Создаем директории
        self._ensure_directories()
        
        # Восстанавливаем состояние при перезапуске
        self._restore_state()
        
        self._log("info", f"📁 Автономный Session Recorder инициализирован")

    def _create_fallback_logger(self):
        """Создает полностью независимый логгер"""
        try:
            logs_dir = "logs"
            if not os.path.exists(logs_dir):
                os.makedirs(logs_dir)
                
            class FallbackLogger:
                def __init__(self, logs_dir):
                    self.logs_dir = logs_dir
                    self.emergency_log = os.path.join(logs_dir, "session_recorder_autonomous.log")
                
                def log(self, level, message):
                    try:
                        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        log_line = f"[{timestamp}] SessionRecorder {level.upper()}: {message}\n"
                        
                        # Записываем в файл
                        with open(self.emergency_log, "a", encoding="utf-8") as f:
                            f.write(log_line)
                        
                        # Также выводим в консоль
                        print(f"📝 {log_line.strip()}")
                    except Exception:
                        # Если даже fallback logger не работает - в консоль
                        print(f"📝 [EMERGENCY] {level.upper()}: {message}")
            
            return FallbackLogger(logs_dir)
        except Exception:
            return None

    def _log(self, level: str, message: str):
        """Максимально безопасное логирование"""
        try:
            # Пытаемся использовать основной логгер
            try:
                from logger import bot_logger
                if level == "info":
                    bot_logger.info(message)
                elif level == "debug":
                    bot_logger.debug(message)
                elif level == "warning":
                    bot_logger.warning(message)
                elif level == "error":
                    bot_logger.error(message)
                return
            except Exception:
                pass
            
            # Fallback logger
            if self.fallback_logger:
                self.fallback_logger.log(level, message)
            else:
                # Последний резерв - консоль
                timestamp = datetime.now().strftime('%H:%M:%S')
                print(f"[{timestamp}] SessionRecorder {level.upper()}: {message}")
                
        except Exception:
            # Абсолютный минимум
            try:
                print(f"📝 {level.upper()}: {message}")
            except Exception:
                pass

    def _ensure_directories(self):
        """Создает необходимые директории"""
        try:
            directories = [self.data_directory, "logs", "session_data/backups"]
            for directory in directories:
                if not os.path.exists(directory):
                    os.makedirs(directory)
                    self._log("debug", f"Создана директория: {directory}")
        except Exception as e:
            self._log("error", f"Ошибка создания директорий: {e}")

    def _restore_state(self):
        """Восстанавливает состояние после перезапуска"""
        try:
            state_file = os.path.join(self.data_directory, "recorder_state.json")
            if os.path.exists(state_file):
                with open(state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    self.recording = state.get('recording', False)
                    if self.recording:
                        self._log("info", "📝 Восстановлен режим записи из сохраненного состояния")
        except Exception as e:
            self._log("error", f"Ошибка восстановления состояния: {e}")

    def _save_state(self):
        """Сохраняет текущее состояние"""
        try:
            state_file = os.path.join(self.data_directory, "recorder_state.json")
            state = {
                'recording': self.recording,
                'active_sessions_count': len(self.active_sessions),
                'last_save': time.time(),
                'emergency_mode': self.emergency_mode
            }
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            self._log("error", f"Ошибка сохранения состояния: {e}")

    def start_recording(self):
        """Запуск записи сессий"""
        try:
            self.recording = True
            self.emergency_mode = False
            self.error_count = 0
            
            # Запускаем автономные процессы
            self._start_autonomous_processes()
            
            self._save_state()
            self._log("info", "📝 Автономный Session Recorder запущен")
        except Exception as e:
            self._log("error", f"Ошибка запуска Session Recorder: {e}")

    def stop_recording(self):
        """Остановка записи сессий"""
        try:
            self.recording = False
            
            # Завершаем все активные сессии
            for symbol in list(self.active_sessions.keys()):
                self._finalize_session(symbol, force=True)
            
            self._save_state()
            self._log("info", "📝 Session Recorder остановлен")
        except Exception as e:
            self._log("error", f"Ошибка остановки Session Recorder: {e}")

    def _start_autonomous_processes(self):
        """Запуск автономных фоновых процессов"""
        try:
            # Автосохранение
            def auto_save_loop():
                while self.recording:
                    try:
                        time.sleep(self.auto_save_interval)
                        if self.recording:
                            self._auto_save_sessions()
                    except Exception as e:
                        self._log("error", f"Ошибка автосохранения: {e}")
            
            # Экстренное сохранение
            def emergency_save_loop():
                while self.recording:
                    try:
                        time.sleep(self.emergency_save_interval)
                        if self.recording:
                            self._emergency_backup()
                    except Exception as e:
                        self._log("error", f"Ошибка экстренного сохранения: {e}")
            
            # Запускаем в отдельных потоках
            self.executor.submit(auto_save_loop)
            self.executor.submit(emergency_save_loop)
            
        except Exception as e:
            self._log("error", f"Ошибка запуска автономных процессов: {e}")

    def update_coin_activity(self, symbol: str, coin_data: Dict):
        """Обновляет активность монеты (максимально защищенная версия)"""
        if not self.recording:
            return
        
        try:
            current_time = time.time()
            is_active = coin_data.get('active', False)
            
            # Защита от переполнения памяти
            if len(self.active_sessions) > self.max_session_memory:
                self._cleanup_old_sessions()
            
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
                        },
                        'backup_count': 0,  # Счетчик резервных копий
                        'last_backup': current_time
                    }
                    self._log("debug", f"📝 Начата запись сессии для {symbol}")
                
                # Обновляем сессию
                session = self.active_sessions[symbol]
                session['last_update'] = current_time
                
                # Добавляем точку данных с защитой от ошибок
                try:
                    data_point = {
                        'timestamp': current_time,
                        'volume': float(coin_data.get('volume', 0)),
                        'trades': int(coin_data.get('trades', 0)),
                        'change': float(coin_data.get('change', 0)),
                        'natr': float(coin_data.get('natr', 0)),
                        'spread': float(coin_data.get('spread', 0)),
                        'price': float(coin_data.get('price', 0))
                    }
                    session['data_points'].append(data_point)
                    
                    # Обновляем сводку
                    self._update_session_summary(session, data_point)
                    
                    # Ограничиваем размер data_points
                    if len(session['data_points']) > 1000:
                        session['data_points'] = session['data_points'][-500:]
                    
                except Exception as e:
                    self._log("error", f"Ошибка обработки данных {symbol}: {e}")
                    
        except Exception as e:
            self.error_count += 1
            self._log("error", f"Критическая ошибка update_coin_activity {symbol}: {e}")
            
            # Включаем emergency mode при множественных ошибках
            if self.error_count > self.max_errors:
                self.emergency_mode = True
                self._emergency_save_all_sessions()

    def _update_session_summary(self, session: Dict, data_point: Dict):
        """Безопасное обновление сводки сессии"""
        try:
            summary = session['summary']
            
            volume = data_point.get('volume', 0)
            trades = data_point.get('trades', 0)
            change = data_point.get('change', 0)
            natr = data_point.get('natr', 0)
            spread = data_point.get('spread', 0)
            price = data_point.get('price', 0)
            
            summary['max_volume'] = max(summary['max_volume'], volume)
            summary['total_volume'] += volume
            summary['total_trades'] += trades
            summary['max_change'] = max(summary['max_change'], change)
            summary['min_change'] = min(summary['min_change'], change)
            summary['max_natr'] = max(summary['max_natr'], natr)
            summary['max_spread'] = max(summary['max_spread'], spread)
            
            if price > 0:
                summary['price_samples'].append(price)
                # Ограничиваем размер price_samples
                if len(summary['price_samples']) > 100:
                    summary['price_samples'] = summary['price_samples'][-50:]
                summary['avg_price'] = sum(summary['price_samples']) / len(summary['price_samples'])
            
            session['total_minutes'] = len(session['data_points'])
            
        except Exception as e:
            self._log("error", f"Ошибка обновления сводки: {e}")

    def check_inactive_sessions(self, active_coins: Dict = None):
        """Проверяет неактивные сессии (защищенная версия)"""
        if not self.recording:
            return
        
        try:
            current_time = time.time()
            inactive_threshold = 120  # 2 минуты для безопасности
            
            sessions_to_finalize = []
            
            for symbol, session in list(self.active_sessions.items()):
                try:
                    # Проверяем время последнего обновления
                    time_since_update = current_time - session.get('last_update', 0)
                    
                    # Проверяем активность через переданный список (если есть)
                    coin_still_active = True
                    if active_coins is not None:
                        coin_still_active = symbol in active_coins
                    
                    # Завершаем сессию если неактивна
                    if not coin_still_active or time_since_update > inactive_threshold:
                        sessions_to_finalize.append(symbol)
                        
                except Exception as e:
                    self._log("error", f"Ошибка проверки сессии {symbol}: {e}")
                    sessions_to_finalize.append(symbol)  # На всякий случай завершаем
            
            # Завершаем неактивные сессии
            for symbol in sessions_to_finalize:
                self._finalize_session(symbol)
                
        except Exception as e:
            self._log("error", f"Критическая ошибка check_inactive_sessions: {e}")

    def _finalize_session(self, symbol: str, force: bool = False):
        """Завершает и сохраняет сессию (максимально защищенная версия)"""
        try:
            if symbol not in self.active_sessions:
                return
            
            session = self.active_sessions[symbol]
            current_time = time.time()
            
            # Рассчитываем длительность
            duration = current_time - session.get('start_time', current_time)
            session['end_time'] = current_time
            session['total_duration'] = duration
            
            # Добавляем временные метки
            try:
                session['start_datetime'] = datetime.fromtimestamp(session['start_time']).isoformat()
                session['end_datetime'] = datetime.fromtimestamp(current_time).isoformat()
            except Exception:
                session['start_datetime'] = "unknown"
                session['end_datetime'] = datetime.now().isoformat()
            
            # Сохраняем сессию
            saved = False
            if duration >= self.session_start_threshold or force:
                saved = self._save_session_to_file(session)
                
                if saved:
                    duration_min = int(duration // 60)
                    duration_sec = int(duration % 60)
                    self._log("info",
                        f"💾 Сессия {symbol} сохранена: {duration_min}м {duration_sec}с, "
                        f"{session.get('total_minutes', 0)} точек данных, "
                        f"макс.объем ${session.get('summary', {}).get('max_volume', 0):,.0f}"
                    )
            
            # Всегда делаем резервную копию в emergency режиме
            if self.emergency_mode or not saved:
                self._emergency_save_session(session)
            
            # Удаляем из активных сессий
            del self.active_sessions[symbol]
            
        except Exception as e:
            self._log("error", f"Критическая ошибка finalize_session {symbol}: {e}")
            # Пытаемся хотя бы удалить из памяти
            try:
                if symbol in self.active_sessions:
                    del self.active_sessions[symbol]
            except Exception:
                pass

    def _save_session_to_file(self, session: Dict) -> bool:
        """Сохраняет сессию в файл с множественными попытками"""
        success = False
        attempts = 3
        
        for attempt in range(attempts):
            try:
                # Определяем дату для файла
                start_time = session.get('start_time', time.time())
                date_str = datetime.fromtimestamp(start_time).strftime('%Y-%m-%d')
                filepath = os.path.join(self.data_directory, f"sessions_{date_str}.json")
                
                # Загружаем существующие данные
                daily_data = self._load_daily_data(filepath, date_str)
                
                # Добавляем новую сессию
                daily_data['sessions'].append(session)
                
                # Обновляем метаданные
                self._update_daily_metadata(daily_data)
                
                # Создаем резервную копию перед сохранением
                backup_path = f"{filepath}.backup_{int(time.time())}"
                if os.path.exists(filepath):
                    try:
                        import shutil
                        shutil.copy2(filepath, backup_path)
                    except Exception:
                        pass
                
                # Сохраняем в файл
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(daily_data, f, indent=2, ensure_ascii=False)
                
                self._log("debug", f"💾 Сессия сохранена в {filepath}")
                success = True
                break
                
            except Exception as e:
                self._log("error", f"Попытка {attempt+1} сохранения сессии {session.get('symbol', 'unknown')}: {e}")
                if attempt == attempts - 1:
                    # Последняя попытка - сохраняем в emergency файл
                    self._emergency_save_session(session)
                    
        return success

    def _load_daily_data(self, filepath: str, date_str: str) -> Dict:
        """Безопасная загрузка дневных данных"""
        try:
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Проверяем структуру
                    if not isinstance(data, dict):
                        raise ValueError("Invalid data format")
                    if 'sessions' not in data:
                        data['sessions'] = []
                    if 'metadata' not in data:
                        data['metadata'] = {}
                    return data
        except Exception as e:
            self._log("warning", f"Ошибка загрузки {filepath}: {e}, создаем новый")
        
        # Возвращаем новую структуру
        return {
            'date': date_str,
            'sessions': [],
            'metadata': {
                'created': datetime.now().isoformat(),
                'file_version': '2.0'
            }
        }

    def _update_daily_metadata(self, daily_data: Dict):
        """Обновляем метаданные дневного файла"""
        try:
            sessions = daily_data.get('sessions', [])
            
            total_sessions = len(sessions)
            total_duration = sum(s.get('total_duration', 0) for s in sessions)
            total_volume = sum(s.get('summary', {}).get('total_volume', 0) for s in sessions)
            unique_symbols = len(set(s.get('symbol') for s in sessions if s.get('symbol')))
            
            daily_data['metadata'] = {
                'total_sessions': total_sessions,
                'total_duration': total_duration,
                'total_volume': total_volume,
                'unique_symbols': unique_symbols,
                'last_updated': datetime.now().isoformat(),
                'file_version': '2.0'
            }
        except Exception as e:
            self._log("error", f"Ошибка обновления метаданных: {e}")

    def _emergency_save_session(self, session: Dict):
        """Экстренное сохранение сессии"""
        try:
            emergency_dir = os.path.join(self.data_directory, "emergency")
            if not os.path.exists(emergency_dir):
                os.makedirs(emergency_dir)
            
            timestamp = int(time.time())
            symbol = session.get('symbol', 'unknown')
            emergency_file = os.path.join(emergency_dir, f"emergency_{symbol}_{timestamp}.json")
            
            with open(emergency_file, 'w', encoding='utf-8') as f:
                json.dump(session, f, indent=2, ensure_ascii=False)
            
            self._log("warning", f"🚨 Экстренное сохранение сессии {symbol} в {emergency_file}")
            
        except Exception as e:
            self._log("error", f"Критическая ошибка экстренного сохранения: {e}")

    def _auto_save_sessions(self):
        """Автоматическое сохранение активных сессий"""
        try:
            if not self.active_sessions:
                return
            
            saved_count = 0
            for symbol, session in list(self.active_sessions.items()):
                try:
                    # Создаем промежуточное сохранение
                    if time.time() - session.get('last_backup', 0) > 60:  # Раз в минуту
                        temp_session = session.copy()
                        temp_session['is_partial'] = True
                        temp_session['auto_save_time'] = time.time()
                        
                        if self._save_session_to_file(temp_session):
                            session['last_backup'] = time.time()
                            saved_count += 1
                            
                except Exception as e:
                    self._log("error", f"Ошибка автосохранения {symbol}: {e}")
            
            if saved_count > 0:
                self._log("debug", f"🔄 Автосохранение: {saved_count} сессий")
                
        except Exception as e:
            self._log("error", f"Критическая ошибка автосохранения: {e}")

    def _emergency_backup(self):
        """Экстренное резервное копирование"""
        try:
            current_time = time.time()
            if current_time - self.last_emergency_save < self.emergency_save_interval:
                return
            
            # Сохраняем состояние
            self._save_state()
            
            # Создаем полный снимок активных сессий
            if self.active_sessions:
                backup_data = {
                    'timestamp': current_time,
                    'active_sessions_count': len(self.active_sessions),
                    'emergency_mode': self.emergency_mode,
                    'sessions_snapshot': list(self.active_sessions.keys())
                }
                
                backup_file = os.path.join(self.data_directory, "recorder_backup.json")
                with open(backup_file, 'w', encoding='utf-8') as f:
                    json.dump(backup_data, f, indent=2)
            
            self.last_emergency_save = current_time
            
        except Exception as e:
            self._log("error", f"Ошибка экстренного бэкапа: {e}")

    def _emergency_save_all_sessions(self):
        """Экстренное сохранение всех активных сессий"""
        try:
            self._log("warning", "🚨 Активирован режим экстренного сохранения всех сессий")
            
            for symbol in list(self.active_sessions.keys()):
                try:
                    self._finalize_session(symbol, force=True)
                except Exception as e:
                    self._log("error", f"Ошибка экстренного сохранения {symbol}: {e}")
            
            self.active_sessions.clear()
            self.emergency_mode = False
            self.error_count = 0
            
        except Exception as e:
            self._log("error", f"Критическая ошибка экстренного сохранения всех сессий: {e}")

    def _cleanup_old_sessions(self):
        """Очистка старых сессий из памяти"""
        try:
            if len(self.active_sessions) <= self.max_session_memory * 0.8:
                return
            
            current_time = time.time()
            old_sessions = []
            
            for symbol, session in self.active_sessions.items():
                last_update = session.get('last_update', 0)
                if current_time - last_update > 300:  # 5 минут
                    old_sessions.append(symbol)
            
            for symbol in old_sessions[:10]:  # Удаляем не более 10 за раз
                self._finalize_session(symbol, force=True)
            
            if old_sessions:
                self._log("info", f"🧹 Очищено {len(old_sessions[:10])} старых сессий из памяти")
                
        except Exception as e:
            self._log("error", f"Ошибка очистки старых сессий: {e}")

    def get_daily_summary(self, date_str: str) -> Optional[Dict]:
        """Возвращает сводку за определенный день"""
        try:
            filepath = os.path.join(self.data_directory, f"sessions_{date_str}.json")
            return self._load_daily_data(filepath, date_str)
        except Exception as e:
            self._log("error", f"Ошибка получения сводки за {date_str}: {e}")
            return None

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику Session Recorder"""
        try:
            return {
                'recording': self.recording,
                'active_sessions': len(self.active_sessions),
                'session_symbols': list(self.active_sessions.keys()),
                'data_directory': self.data_directory,
                'emergency_mode': self.emergency_mode,
                'error_count': self.error_count,
                'autonomous': True,
                'version': '2.0_autonomous'
            }
        except Exception as e:
            self._log("error", f"Ошибка получения статистики: {e}")
            return {
                'recording': False,
                'active_sessions': 0,
                'session_symbols': [],
                'data_directory': self.data_directory,
                'error': str(e)
            }

    def force_save_all(self):
        """Принудительное сохранение всех активных сессий"""
        try:
            self._log("info", "🔄 Принудительное сохранение всех активных сессий")
            saved_count = 0
            
            for symbol in list(self.active_sessions.keys()):
                try:
                    session = self.active_sessions[symbol]
                    if self._save_session_to_file(session):
                        saved_count += 1
                except Exception as e:
                    self._log("error", f"Ошибка принудительного сохранения {symbol}: {e}")
            
            self._log("info", f"✅ Принудительно сохранено {saved_count} сессий")
            return saved_count
            
        except Exception as e:
            self._log("error", f"Критическая ошибка принудительного сохранения: {e}")
            return 0

    def __del__(self):
        """Деструктор с защитой данных"""
        try:
            if hasattr(self, 'active_sessions') and self.active_sessions:
                self._emergency_save_all_sessions()
            if hasattr(self, 'executor'):
                self.executor.shutdown(wait=False)
        except Exception:
            pass


# Создаем глобальный экземпляр с новым именем для обратной совместимости
session_recorder = AutonomousSessionRecorder()

# Алиас для обратной совместимости
SessionRecorder = AutonomousSessionRecorder
