"""
Модуль управления пользователями для многопользовательского режима
"""

import json
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Any
from logger import bot_logger


class UserManager:
    def __init__(self, data_file: str = "users_data.json"):
        self.data_file = data_file
        self.users_data: Dict[str, Dict] = {}
        self.pending_requests: Dict[str, Dict] = {}
        self.admin_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        # Убрали персональные настройки - используем только глобальные
        self.default_user_config = {}
        self.load_data()
        self._ensure_admin_data()

    def load_data(self):
        """Загружает данные пользователей из файла"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.users_data = data.get('users', {})
                    self.pending_requests = data.get('pending_requests', {})
                    self.rejected_users = data.get('rejected_users', {})
                bot_logger.info("Данные пользователей загружены")
            else:
                self.users_data = {}
                self.pending_requests = {}
                self.rejected_users = {}
                self.save_data()
                bot_logger.info("Создан новый файл данных пользователей")
        except Exception as e:
            bot_logger.error(f"Ошибка загрузки данных пользователей: {e}")
            self.users_data = {}
            self.pending_requests = {}
            self.rejected_users = {}

    def save_data(self):
        """Сохраняет данные пользователей в файл"""
        try:
            data = {
                'users': self.users_data,
                'pending_requests': self.pending_requests,
                'rejected_users': getattr(self, 'rejected_users', {}),
                'last_updated': datetime.now().isoformat()
            }
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            bot_logger.debug("Данные пользователей сохранены")
        except Exception as e:
            bot_logger.error(f"Ошибка сохранения данных пользователей: {e}")

    def is_admin(self, chat_id: str) -> bool:
        """Проверяет, является ли пользователь администратором"""
        return str(chat_id) == str(self.admin_chat_id)

    def is_user_approved(self, chat_id: str) -> bool:
        """Проверяет, одобрен ли пользователь"""
        return str(chat_id) in self.users_data

    def is_user_pending(self, chat_id: str) -> bool:
        """Проверяет, находится ли пользователь в ожидании одобрения"""
        return str(chat_id) in self.pending_requests

    def add_pending_request(self, chat_id: str, user_info: Dict):
        """Добавляет заявку на подключение"""
        chat_id_str = str(chat_id)

        if self.is_admin(chat_id) or self.is_user_approved(chat_id):
            return False  # Админ или уже одобренный пользователь

        # Проверяем, был ли пользователь ранее отклонен
        rejected_data = getattr(self, 'rejected_users', {}).get(chat_id_str)

        self.pending_requests[chat_id_str] = {
            'chat_id': chat_id_str,
            'username': user_info.get('username', 'Unknown'),
            'first_name': user_info.get('first_name', 'Unknown'),
            'last_name': user_info.get('last_name', ''),
            'request_time': time.time(),
            'request_datetime': datetime.now().isoformat(),
            'is_returning_user': rejected_data is not None,
            'previous_rejections': rejected_data.get('rejection_count', 0) if rejected_data else 0
        }
        self.save_data()
        bot_logger.info(f"Добавлена заявка от пользователя {chat_id_str}")
        return True

    def approve_user(self, chat_id: str) -> bool:
        """Одобряет пользователя"""
        chat_id_str = str(chat_id)

        if chat_id_str not in self.pending_requests:
            return False

        # Переносим данные из заявки в одобренные пользователи
        request_data = self.pending_requests[chat_id_str]

        self.users_data[chat_id_str] = {
            'chat_id': chat_id_str,
            'username': request_data.get('username', 'Unknown'),
            'first_name': request_data.get('first_name', 'Unknown'),
            'last_name': request_data.get('last_name', ''),
            'approved_time': time.time(),
            'approved_datetime': datetime.now().isoformat(),
            'setup_completed': False,
            'watchlist': [],
            'active_coins': {},
            'last_activity': time.time(),
            'setup_state': ''
        }

        # Удаляем из заявок
        del self.pending_requests[chat_id_str]
        self.save_data()

        bot_logger.info(f"Пользователь {chat_id_str} одобрен")
        return True

    def reject_user(self, chat_id: str) -> bool:
        """Отклоняет заявку пользователя, но сохраняет информацию для возможного повторного доступа"""
        chat_id_str = str(chat_id)

        if chat_id_str not in self.pending_requests:
            return False

        # Сохраняем информацию об отклоненном пользователе
        request_data = self.pending_requests[chat_id_str]

        # Создаем запись об отклоненном пользователе
        if not hasattr(self, 'rejected_users'):
            self.rejected_users = {}

        self.rejected_users[chat_id_str] = {
            'chat_id': chat_id_str,
            'username': request_data.get('username', 'Unknown'),
            'first_name': request_data.get('first_name', 'Unknown'),
            'last_name': request_data.get('last_name', ''),
            'first_request_time': request_data.get('request_time'),
            'rejected_time': time.time(),
            'rejected_datetime': datetime.now().isoformat(),
            'rejection_count': self.rejected_users.get(chat_id_str, {}).get('rejection_count', 0) + 1
        }

        del self.pending_requests[chat_id_str]
        self.save_data()

        bot_logger.info(f"Заявка пользователя {chat_id_str} отклонена")
        return True

    def get_pending_requests(self) -> List[Dict]:
        """Возвращает список заявок на подключение"""
        return list(self.pending_requests.values())

    def get_user_data(self, chat_id: str) -> Optional[Dict]:
        """Возвращает данные пользователя"""
        chat_id_str = str(chat_id)
        return self.users_data.get(chat_id_str)

    def update_user_data(self, chat_id: str, data: Dict):
        """Обновляет данные пользователя"""
        chat_id_str = str(chat_id)
        if chat_id_str in self.users_data:
            self.users_data[chat_id_str].update(data)
            self.users_data[chat_id_str]['last_activity'] = time.time()
            self.save_data()

    def mark_setup_completed(self, chat_id: str):
        """Отмечает, что пользователь завершил настройку"""
        chat_id_str = str(chat_id)
        if chat_id_str in self.users_data:
            self.users_data[chat_id_str]['setup_completed'] = True
            self.save_data()

    def is_setup_completed(self, chat_id: str) -> bool:
        """Проверяет, завершил ли пользователь настройку"""
        user_data = self.get_user_data(chat_id)
        return user_data and user_data.get('setup_completed', False)

    def get_user_watchlist(self, chat_id: str) -> List[str]:
        """Возвращает список монет пользователя"""
        user_data = self.get_user_data(chat_id)
        return user_data.get('watchlist', []) if user_data else []

    def add_user_coin(self, chat_id: str, symbol: str) -> bool:
        """Добавляет монету в список пользователя"""
        user_data = self.get_user_data(chat_id)
        if not user_data:
            return False

        watchlist = user_data.get('watchlist', [])
        if symbol not in watchlist:
            watchlist.append(symbol)
            self.update_user_data(chat_id, {'watchlist': watchlist})
            return True
        return False

    def remove_user_coin(self, chat_id: str, symbol: str) -> bool:
        """Удаляет монету из списка пользователя"""
        user_data = self.get_user_data(chat_id)
        if not user_data:
            return False

        watchlist = user_data.get('watchlist', [])
        if symbol in watchlist:
            watchlist.remove(symbol)
            self.update_user_data(chat_id, {'watchlist': watchlist})
            return True
        return False

    

    def get_all_users(self) -> List[Dict]:
        """Возвращает всех одобренных пользователей"""
        return list(self.users_data.values())

    def revoke_user_access(self, chat_id: str) -> bool:
        """Отключает доступ пользователя"""
        chat_id_str = str(chat_id)

        if chat_id_str not in self.users_data:
            return False

        # Удаляем пользователя из системы
        del self.users_data[chat_id_str]
        self.save_data()

        bot_logger.info(f"Доступ пользователя {chat_id_str} отключен")
        return True

    def is_returning_user(self, chat_id: str) -> bool:
        """Проверяет, является ли пользователь возвращающимся (ранее отклоненным)"""
        chat_id_str = str(chat_id)
        return chat_id_str in getattr(self, 'rejected_users', {})

    def get_rejected_user_info(self, chat_id: str) -> Optional[Dict]:
        """Возвращает информацию об отклоненном пользователе"""
        chat_id_str = str(chat_id)
        return getattr(self, 'rejected_users', {}).get(chat_id_str)

    

    

    def clear_all_users_except_admin(self) -> int:
        """Очищает всех пользователей кроме администратора"""
        cleared_count = 0

        # Подсчитываем количество пользователей для удаления
        users_to_remove = [chat_id for chat_id in self.users_data.keys() 
                          if not self.is_admin(chat_id)]
        cleared_count = len(users_to_remove)

        # Удаляем всех пользователей кроме админа
        for chat_id in users_to_remove:
            del self.users_data[chat_id]

        # Очищаем заявки
        self.pending_requests.clear()

        # Очищаем отклоненных пользователей
        if hasattr(self, 'rejected_users'):
            self.rejected_users.clear()

        # Сохраняем изменения
        self.save_data()

        bot_logger.info(f"Очищено {cleared_count} пользователей, оставлен только администратор")
        return cleared_count

    def get_stats(self) -> Dict:
        """Возвращает статистику пользователей"""
        return {
            'total_users': len(self.users_data),
            'pending_requests': len(self.pending_requests),
            'rejected_users': len(getattr(self, 'rejected_users', {})),
            'completed_setup': len([u for u in self.users_data.values() if u.get('setup_completed', False)]),
            'admin_chat_id': self.admin_chat_id
        }

    def add_coin_to_user_watchlist(self, chat_id: str, symbol: str) -> bool:
        """Добавляет монету в список пользователя (alias для add_user_coin)"""
        return self.add_user_coin(chat_id, symbol)

    def remove_coin_from_user_watchlist(self, chat_id: str, symbol: str) -> bool:
        """Удаляет монету из списка пользователя (alias для remove_user_coin)"""
        return self.remove_user_coin(chat_id, symbol)

    def get_user_config(self, chat_id: str) -> Dict:
        """Возвращает конфигурацию пользователя с значениями по умолчанию"""
        # Все пользователи используют глобальные настройки из config
        from config import config_manager
        return {
            'VOLUME_THRESHOLD': config_manager.get('VOLUME_THRESHOLD', 1000),
            'SPREAD_THRESHOLD': config_manager.get('SPREAD_THRESHOLD', 0.1),
            'NATR_THRESHOLD': config_manager.get('NATR_THRESHOLD', 0.5)
        }

    def _ensure_admin_data(self):
        """Обеспечивает наличие данных администратора"""
        if self.admin_chat_id and str(self.admin_chat_id) not in self.users_data:
            bot_logger.info(f"Создаем данные для администратора: {self.admin_chat_id}")
            self.users_data[str(self.admin_chat_id)] = {
                'chat_id': str(self.admin_chat_id),
                'username': 'admin',
                'first_name': 'Administrator',
                'last_name': '',
                'approved_time': time.time(),
                'approved_datetime': datetime.now().isoformat(),
                'setup_completed': True,
                'watchlist': [],
                'active_coins': {},
                'last_activity': time.time(),
                'setup_state': 'completed'
            }
            self.save_data()
            bot_logger.info("Данные администратора созданы")

    


# Глобальный экземпляр менеджера пользователей
user_manager = UserManager()