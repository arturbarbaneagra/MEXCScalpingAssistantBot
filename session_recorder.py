"""
Модуль записи сессий активных монет
Сохраняет данные о каждой активной сессии монеты в отдельные файлы по дням
"""

import os
import json
import time
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
from logger import bot_logger
from config import config_manager


class SessionRecorder:
    def __init__(self):
        self.recording = False
        self.active_sessions: Dict[str, Dict] = {}
        self.user_session_recorders: Dict[str, 'UserSessionRecorder'] = {}
        self.session_start_threshold = 60  # Минимум 60 секунд для записи сессии

    def start_recording(self):
        """Запуск записи сессий"""
        self.recording = True
        
        # Инициализируем рекордеры для всех зарегистрированных пользователей
        try:
            from user_manager import user_manager
            all_users = user_manager.get_all_users()
            
            for user_data in all_users:
                chat_id = user_data['chat_id']
                user_recorder = self.get_user_session_recorder(chat_id)
                bot_logger.info(f"📝 Session Recorder запущен для пользователя {chat_id}")
                
        except Exception as e:
            bot_logger.warning(f"Ошибка инициализации пользовательских рекордеров: {e}")
        
        # Запускаем запись для всех существующих пользовательских рекордеров
        for user_recorder in self.user_session_recorders.values():
            user_recorder.start_recording()
            
        bot_logger.info("📝 Session Recorder запущен для всех пользователей")

    def stop_recording(self):
        """Остановка записи сессий"""
        self.recording = False

        # Останавливаем запись для всех пользовательских рекордеров
        for user_recorder in self.user_session_recorders.values():
            user_recorder.stop_recording()

        bot_logger.info("📝 Session Recorder остановлен")

    def get_user_session_recorder(self, chat_id: str):
        """Получает или создает сессионный рекордер для пользователя"""
        from user_session_recorder import UserSessionRecorder
        
        chat_id_str = str(chat_id)
        if chat_id_str not in self.user_session_recorders:
            self.user_session_recorders[chat_id_str] = UserSessionRecorder(chat_id_str)
            if self.recording:
                self.user_session_recorders[chat_id_str].start_recording()
        return self.user_session_recorders[chat_id_str]

    def update_coin_activity(self, symbol: str, coin_data: Dict):
        """Обновляет активность монеты для всех пользователей"""
        if not self.recording:
            return

        try:
            from user_manager import user_manager
            
            # Получаем всех активных пользователей
            all_users = user_manager.get_all_users()
            
            for user_data in all_users:
                chat_id = user_data['chat_id']
                
                # Проверяем, отслеживает ли пользователь эту монету
                if user_manager.is_admin(chat_id):
                    # Для админа проверяем глобальный список
                    from watchlist_manager import watchlist_manager
                    user_watchlist = watchlist_manager.get_all()
                else:
                    # Для обычных пользователей проверяем их личный список
                    user_watchlist = user_manager.get_user_watchlist(chat_id)
                
                if symbol in user_watchlist:
                    # Получаем рекордер пользователя и обновляем активность
                    user_recorder = self.get_user_session_recorder(chat_id)
                    user_recorder.update_coin_activity(symbol, coin_data)
                    
        except Exception as e:
            bot_logger.debug(f"Ошибка обновления активности монеты {symbol}: {e}")

    def check_inactive_sessions(self, active_coins: Dict):
        """Проверяет неактивные сессии и завершает их для всех пользователей"""
        if not self.recording:
            return

        # Проверяем неактивные сессии для каждого пользователя
        for user_recorder in self.user_session_recorders.values():
            user_recorder.check_inactive_sessions(active_coins)

    def get_daily_summary(self, date_str: str, chat_id: str = None) -> Optional[Dict]:
        """Возвращает сводку за определенный день для конкретного пользователя"""
        if chat_id:
            user_recorder = self.get_user_session_recorder(chat_id)
            return user_recorder.get_daily_summary(date_str)
        else:
            # Для обратной совместимости - возвращаем данные админа
            from user_manager import user_manager
            admin_chat_id = user_manager.admin_chat_id
            user_recorder = self.get_user_session_recorder(admin_chat_id)
            return user_recorder.get_daily_summary(date_str)

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику Session Recorder"""
        total_active_sessions = sum(
            len(recorder.active_sessions) 
            for recorder in self.user_session_recorders.values()
        )
        
        all_session_symbols = []
        for recorder in self.user_session_recorders.values():
            all_session_symbols.extend(recorder.active_sessions.keys())
        
        return {
            'recording': self.recording,
            'active_sessions': total_active_sessions,
            'session_symbols': list(set(all_session_symbols)),
            'user_recorders_count': len(self.user_session_recorders),
            'users': list(self.user_session_recorders.keys())
        }

    def get_user_stats(self, chat_id: str) -> Dict[str, Any]:
        """Возвращает статистику Session Recorder для конкретного пользователя"""
        chat_id_str = str(chat_id)
        if chat_id_str in self.user_session_recorders:
            return self.user_session_recorders[chat_id_str].get_stats()
        else:
            return {
                'recording': False,
                'active_sessions': 0,
                'session_symbols': [],
                'data_directory': f"user_sessions_{chat_id_str}",
                'chat_id': chat_id_str
            }

    def update_activity_stats(self, sessions: list):
        """Обновляет статистику активности"""
        # Обновляем статистику активности для всех пользователей
        try:
            from user_activity_calculator import user_activity_manager
            from user_manager import user_manager

            # Получаем сессии для текущего часа
            current_hour = datetime.now().replace(minute=0, second=0, microsecond=0)
            hour_sessions = [s for s in sessions if
                           current_hour <= datetime.fromtimestamp(s.get('start_time', 0)) < current_hour + timedelta(hours=1)]

            if hour_sessions:
                # Обновляем статистику для каждого пользователя
                all_users = user_manager.get_all_users()
                for user_data in all_users:
                    chat_id = user_data['chat_id']

                    # Получаем сессии пользователя за этот час
                    user_hour_sessions = []
                    user_data_dir = f"user_sessions_{chat_id}"
                    if os.path.exists(user_data_dir):
                        date_str = current_hour.strftime('%Y-%m-%d')
                        user_filepath = os.path.join(user_data_dir, f"sessions_{date_str}.json")

                        if os.path.exists(user_filepath):
                            try:
                                with open(user_filepath, 'r', encoding='utf-8') as f:
                                    user_daily_data = json.load(f)

                                cutoff_start = current_hour.timestamp()
                                cutoff_end = cutoff_start + 3600

                                user_hour_sessions = [s for s in user_daily_data.get('sessions', [])
                                                    if cutoff_start <= s.get('start_time', 0) < cutoff_end]
                            except Exception as e:
                                bot_logger.debug(f"Ошибка чтения файла пользователя {chat_id}: {e}")

                    if user_hour_sessions:
                        calculator = user_activity_manager.get_user_calculator(chat_id)
                        hourly_activity = calculator.calculate_hourly_activity(user_hour_sessions, current_hour)
                        calculator.update_activity_stats(hourly_activity)

        except Exception as e:
            bot_logger.warning(f"Ошибка обновления статистики активности: {e}")


# Глобальный экземпляр
session_recorder = SessionRecorder()