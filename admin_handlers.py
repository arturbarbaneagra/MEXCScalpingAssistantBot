
"""
Административные обработчики для многопользовательского бота
"""

import os
import time
from datetime import datetime, timedelta
from typing import Dict, List
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from logger import bot_logger
from user_manager import user_manager


class AdminHandlers:
    def __init__(self, bot_instance):
        self.bot = bot_instance

    def get_admin_keyboard(self) -> ReplyKeyboardMarkup:
        """Возвращает клавиатуру администратора"""
        return ReplyKeyboardMarkup([
            ["🔔 Уведомления", "📊 Мониторинг"],
            ["➕ Добавить", "➖ Удалить"],
            ["📋 Список", "⚙ Настройки"],
            ["📈 Активность 24ч", "ℹ Статус"],
            ["👥 Список заявок", "📋 Логи"],
            ["👤 Управление пользователями", "🛑 Стоп"]
        ], resize_keyboard=True, one_time_keyboard=False)

    async def handle_pending_requests(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопки 'Список заявок'"""
        if not user_manager.is_admin(update.effective_chat.id):
            await update.message.reply_text("❌ У вас нет прав администратора")
            return

        pending_requests = user_manager.get_pending_requests()
        
        if not pending_requests:
            await update.message.reply_text(
                "📭 <b>Нет заявок на подключение</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_admin_keyboard()
            )
            return

        text = f"👥 <b>Заявки на подключение ({len(pending_requests)}):</b>\n\n"
        
        keyboard = []
        
        for request in pending_requests:
            username = request.get('username', 'Unknown')
            first_name = request.get('first_name', 'Unknown')
            request_time = datetime.fromisoformat(request['request_datetime']).strftime('%d.%m %H:%M')
            
            text += (
                f"👤 <b>{first_name}</b>\n"
                f"• Username: @{username}\n"
                f"• ID: <code>{request['chat_id']}</code>\n"
                f"• Время: {request_time}\n\n"
            )
            
            # Создаем инлайн кнопки для каждой заявки
            row = [
                InlineKeyboardButton(
                    f"✅ Принять {first_name}", 
                    callback_data=f"approve_{request['chat_id']}"
                ),
                InlineKeyboardButton(
                    f"❌ Отказать {first_name}", 
                    callback_data=f"reject_{request['chat_id']}"
                )
            ]
            keyboard.append(row)

        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

    async def handle_approve_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: str):
        """Обработчик одобрения пользователя"""
        if not user_manager.is_admin(update.effective_chat.id):
            await update.callback_query.answer("❌ У вас нет прав администратора")
            return

        if user_manager.approve_user(chat_id):
            # Уведомляем пользователя об одобрении
            try:
                await self.bot.app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "🎉 <b>Поздравляем! Ваша заявка одобрена!</b>\n\n"
                        "Теперь вам нужно настроить бота:\n\n"
                        "1️⃣ Добавьте хотя бы одну монету в свой список\n"
                        "2️⃣ Настройте фильтры (объём, спред, NATR)\n\n"
                        "После этого вам станут доступны все функции бота!\n\n"
                        "Для начала нажмите ➕ <b>Добавить</b>"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=self._get_user_keyboard()
                )
                
                await update.callback_query.edit_message_text(
                    text=f"✅ Пользователь {chat_id} одобрен и уведомлен",
                    parse_mode=ParseMode.HTML
                )
                
            except Exception as e:
                bot_logger.error(f"Ошибка уведомления пользователя {chat_id}: {e}")
                await update.callback_query.edit_message_text(
                    text=f"✅ Пользователь {chat_id} одобрен, но не удалось отправить уведомление"
                )
        else:
            await update.callback_query.answer("❌ Ошибка при одобрении пользователя")

    async def handle_reject_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: str):
        """Обработчик отклонения пользователя"""
        if not user_manager.is_admin(update.effective_chat.id):
            await update.callback_query.answer("❌ У вас нет прав администратора")
            return

        if user_manager.reject_user(chat_id):
            # Уведомляем пользователя об отклонении
            try:
                await self.bot.app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "😔 <b>К сожалению, ваша заявка отклонена</b>\n\n"
                        "Вы можете подать новую заявку позже, нажав /start"
                    ),
                    parse_mode=ParseMode.HTML
                )
                
                await update.callback_query.edit_message_text(
                    text=f"❌ Заявка пользователя {chat_id} отклонена"
                )
                
            except Exception as e:
                bot_logger.error(f"Ошибка уведомления пользователя {chat_id}: {e}")
                await update.callback_query.edit_message_text(
                    text=f"❌ Заявка пользователя {chat_id} отклонена, но не удалось отправить уведомление"
                )
        else:
            await update.callback_query.answer("❌ Ошибка при отклонении заявки")

    async def handle_logs_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопки 'Логи' - отправляет логи за последние 2 дня"""
        if not user_manager.is_admin(update.effective_chat.id):
            await update.message.reply_text("❌ У вас нет прав администратора")
            return

        try:
            # Ищем файлы логов
            log_files = []
            
            # Основной лог
            if os.path.exists("trading_bot.log"):
                log_files.append(("trading_bot.log", "Основной лог"))
            
            # Ротированные логи (последние 2)
            for i in range(1, 3):
                log_file = f"trading_bot.log.{i}"
                if os.path.exists(log_file):
                    log_files.append((log_file, f"Лог {i}"))

            if not log_files:
                await update.message.reply_text(
                    "📋 Файлы логов не найдены",
                    reply_markup=self.get_admin_keyboard()
                )
                return

            await update.message.reply_text(
                f"📋 <b>Отправляю логи за последние дни...</b>\n\n"
                f"Найдено файлов: {len(log_files)}",
                parse_mode=ParseMode.HTML
            )

            # Отправляем каждый файл лога
            for log_file, description in log_files:
                try:
                    # Проверяем размер файла
                    file_size = os.path.getsize(log_file)
                    
                    if file_size > 50 * 1024 * 1024:  # 50MB лимит Telegram
                        # Если файл слишком большой, отправляем последние строки
                        with open(log_file, 'r', encoding='utf-8') as f:
                            lines = f.readlines()
                            last_lines = lines[-1000:]  # Последние 1000 строк
                            
                        content = ''.join(last_lines)
                        
                        # Создаем временный файл
                        temp_file = f"temp_{log_file}"
                        with open(temp_file, 'w', encoding='utf-8') as f:
                            f.write(f"=== ПОСЛЕДНИЕ 1000 СТРОК ИЗ {log_file} ===\n\n")
                            f.write(content)
                        
                        with open(temp_file, 'rb') as f:
                            await update.message.reply_document(
                                document=f,
                                caption=f"📋 {description} (последние 1000 строк)",
                                filename=f"last1000_{log_file}"
                            )
                        
                        # Удаляем временный файл
                        os.remove(temp_file)
                    else:
                        # Отправляем весь файл
                        with open(log_file, 'rb') as f:
                            await update.message.reply_document(
                                document=f,
                                caption=f"📋 {description} ({file_size // 1024} KB)",
                                filename=log_file
                            )
                    
                    await context.bot.send_chat_action(
                        chat_id=update.effective_chat.id, 
                        action="upload_document"
                    )
                    
                except Exception as e:
                    bot_logger.error(f"Ошибка отправки лога {log_file}: {e}")
                    await update.message.reply_text(
                        f"❌ Ошибка отправки {description}: {str(e)[:100]}"
                    )

            await update.message.reply_text(
                "✅ <b>Логи отправлены</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_admin_keyboard()
            )

        except Exception as e:
            bot_logger.error(f"Ошибка обработки запроса логов: {e}")
            await update.message.reply_text(
                f"❌ Ошибка получения логов: {str(e)[:100]}",
                reply_markup=self.get_admin_keyboard()
            )

    async def handle_user_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик управления пользователями"""
        if not user_manager.is_admin(update.effective_chat.id):
            await update.message.reply_text("❌ У вас нет прав администратора")
            return

        stats = user_manager.get_stats()
        users = user_manager.get_all_users()
        
        text = (
            f"👥 <b>Управление пользователями</b>\n\n"
            f"📊 <b>Статистика:</b>\n"
            f"• Всего пользователей: {stats['total_users']}\n"
            f"• Заявок в ожидании: {stats['pending_requests']}\n"
            f"• Завершили настройку: {stats['completed_setup']}\n\n"
        )
        
        if users:
            text += "👤 <b>Активные пользователи:</b>\n"
            for user in users[:10]:  # Показываем первых 10
                setup_status = "✅" if user.get('setup_completed', False) else "⚙️"
                watchlist_count = len(user.get('watchlist', []))
                last_activity = datetime.fromtimestamp(user['last_activity']).strftime('%d.%m %H:%M')
                
                text += (
                    f"{setup_status} <b>{user['first_name']}</b> "
                    f"(@{user.get('username', 'no_username')})\n"
                    f"   • Монет: {watchlist_count} • Активность: {last_activity}\n"
                )
            
            if len(users) > 10:
                text += f"\n... и еще {len(users) - 10} пользователей"

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=self.get_admin_keyboard()
        )

    def _get_user_keyboard(self) -> ReplyKeyboardMarkup:
        """Возвращает клавиатуру обычного пользователя"""
        return ReplyKeyboardMarkup([
            ["🔔 Уведомления", "📊 Мониторинг"],
            ["➕ Добавить", "➖ Удалить"],
            ["📋 Список", "⚙ Настройки"],
            ["📈 Активность 24ч", "ℹ Статус"],
            ["🛑 Стоп"]
        ], resize_keyboard=True, one_time_keyboard=False)


# Функция для создания экземпляра админских обработчиков
def create_admin_handlers(bot_instance):
    """Создает экземпляр админских обработчиков"""
    return AdminHandlers(bot_instance)
