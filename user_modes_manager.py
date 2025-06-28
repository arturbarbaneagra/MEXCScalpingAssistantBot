"""
Менеджер режимов для администратора
Упрощенная версия без персональных режимов пользователей
"""

import asyncio
import time
from typing import Dict, Optional, Any
from logger import bot_logger
from user_manager import user_manager


class AdminModesManager:
    """Менеджер режимов только для администратора"""

    def __init__(self, bot_instance):
        self.bot = bot_instance
        self.admin_modes: Dict[str, Any] = {}

    async def start_admin_mode(self, mode_type: str) -> bool:
        """Запускает режим для администратора"""
        admin_chat_id = user_manager.admin_chat_id

        if not admin_chat_id:
            return False

        # Останавливаем текущий режим если есть
        await self.stop_admin_mode()

        # Запускаем новый режим через основной бот
        if mode_type == 'notification':
            success = await self.bot.start_notification_mode()
        elif mode_type == 'monitoring':
            success = await self.bot.start_monitoring_mode()
        else:
            return False

        if success:
            self.admin_modes[mode_type] = {
                'start_time': time.time(),
                'running': True
            }
            bot_logger.info(f"Режим {mode_type} запущен для администратора")

        return success

    async def stop_admin_mode(self) -> bool:
        """Останавливает текущий режим администратора"""
        stopped_any = False

        # Останавливаем все режимы через основной бот
        if hasattr(self.bot, 'notification_mode') and self.bot.notification_mode.running:
            await self.bot.stop_notification_mode()
            stopped_any = True

        if hasattr(self.bot, 'monitoring_mode') and self.bot.monitoring_mode.running:
            await self.bot.stop_monitoring_mode()
            stopped_any = True

        # Очищаем состояние
        self.admin_modes.clear()

        if stopped_any:
            bot_logger.info("Режимы администратора остановлены")

        return stopped_any

    def get_admin_mode(self) -> Optional[str]:
        """Возвращает текущий режим администратора"""
        for mode_type in self.admin_modes:
            if self.admin_modes[mode_type].get('running', False):
                return mode_type
        return None

    def is_admin_mode_running(self, mode_type: str = None) -> bool:
        """Проверяет, запущен ли режим у администратора"""
        if mode_type:
            return mode_type in self.admin_modes and self.admin_modes[mode_type].get('running', False)
        else:
            return any(mode_data.get('running', False) for mode_data in self.admin_modes.values())

    def get_admin_stats(self) -> Dict:
        """Возвращает статистику режимов администратора"""
        return {
            'admin_chat_id': user_manager.admin_chat_id,
            'modes': self.admin_modes
        }

    async def stop_all_modes(self):
        """Останавливает все режимы"""
        await self.stop_admin_mode()


# Глобальный экземпляр менеджера (будет инициализирован в telegram_bot.py)
user_modes_manager = None