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
            ["👤 Управление пользователями", "🧹 Очистить пользователей"],
            ["🛑 Стоп"]
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
            # Отправляем уведомление одобренному пользователю
            await self.bot.app.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🎉 <b>Поздравляем! Ваша заявка одобрена!</b>\n\n"
                    "👋 <b>Добро пожаловать!</b>\n\n"
                    "Чтобы начать, добавьте хотя бы одну монету для отслеживания.\n\n"
                    "Введите символ монеты (например: BTC, ETH, ADA):"
                ),
                parse_mode=ParseMode.HTML
            )

            await update.callback_query.edit_message_text(
                text=f"✅ Пользователь {chat_id} одобрен и уведомлен",
                parse_mode=ParseMode.HTML
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

    async def handle_clear_all_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик очистки всех пользователей кроме админа"""
        if not user_manager.is_admin(update.effective_chat.id):
            await update.message.reply_text("❌ У вас нет прав администратора")
            return

        # Получаем статистику до очистки
        stats_before = user_manager.get_stats()
        
        # Очищаем всех пользователей кроме админа
        cleared_count = user_manager.clear_all_users_except_admin()
        
        # Останавливаем все пользовательские режимы
        if hasattr(self.bot, 'user_modes_manager') and self.bot.user_modes_manager:
            await self.bot.user_modes_manager.stop_all_modes()
        
        await update.message.reply_text(
            f"🧹 <b>Очистка пользователей завершена</b>\n\n"
            f"📊 <b>Результат:</b>\n"
            f"• Удалено пользователей: {cleared_count}\n"
            f"• Удалено заявок: {stats_before['pending_requests']}\n"
            f"• Удалено отклоненных: {stats_before.get('rejected_users', 0)}\n\n"
            f"✅ Остались только данные администратора",
            parse_mode=ParseMode.HTML,
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

        keyboard = []

        if users:
            text += "👤 <b>Активные пользователи:</b>\n"
            for user in users[:10]:  # Показываем первых 10
                setup_status = "✅" if user.get('setup_completed', False) else "⚙️"
                watchlist_count = len(user.get('watchlist', []))
                last_activity = datetime.fromtimestamp(user['last_activity']).strftime('%d.%m %H:%M')

                user_data = user_manager.get_user_data(user['chat_id'])
                user_config = user_data.get('config', {}) if user_data else {}

                # Получаем список монет пользователя
                user_watchlist = user_manager.get_user_watchlist(user['chat_id'])

                # Получаем информацию о текущем режиме пользователя
                current_mode = self.bot.user_modes_manager.get_user_mode(user['chat_id'])
                mode_status = f"🟢 {current_mode}" if current_mode else "🔴 остановлен"

                text += (
                    f"👤 <b>{user['first_name']}</b>\n"
                    f"• ID: <code>{user['chat_id']}</code>\n"
                    f"• Username: @{user.get('username', 'не указан')}\n"
                    f"• Режим: {mode_status}\n"
                    f"• Монет: {len(user_watchlist)}\n"
                    f"• Настройки: V${user_config.get('VOLUME_THRESHOLD', 1000)}, "
                    f"S{user_config.get('SPREAD_THRESHOLD', 0.1)}%, "
                    f"N{user_config.get('NATR_THRESHOLD', 0.5)}%\n\n"
                )

                # Создаем инлайн кнопку для отключения пользователя
                row = [
                    InlineKeyboardButton(
                        f"🚫 Отключить {user['first_name']}", 
                        callback_data=f"revoke_{user['chat_id']}"
                    )
                ]
                keyboard.append(row)

            if len(users) > 10:
                text += f"\n... и еще {len(users) - 10} пользователей"

        # Добавляем кнопку для показа всех пользователей если их больше 10
        if len(users) > 10:
            keyboard.append([
                InlineKeyboardButton(
                    f"📋 Показать всех пользователей ({len(users)})", 
                    callback_data="show_all_users"
                )
            ])

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

    async def handle_revoke_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: str):
        """Обработчик отключения пользователя"""
        if not user_manager.is_admin(update.effective_chat.id):
            await update.callback_query.answer("❌ У вас нет прав администратора")
            return

        if user_manager.revoke_user_access(chat_id):
            # Уведомляем пользователя об отключении
            try:
                await self.bot.app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "🚫 <b>Ваш доступ к боту отключен</b>\n\n"
                        "Администратор отозвал ваши права доступа.\n"
                        "Для восстановления доступа обратитесь к администратору."
                    ),
                    parse_mode=ParseMode.HTML
                )

                await update.callback_query.edit_message_text(
                    text=f"🚫 Доступ пользователя {chat_id} отключен и он уведомлен",
                    parse_mode=ParseMode.HTML
                )

            except Exception as e:
                bot_logger.error(f"Ошибка уведомления пользователя {chat_id} об отключении: {e}")
                await update.callback_query.edit_message_text(
                    text=f"🚫 Доступ пользователя {chat_id} отключен, но не удалось отправить уведомление"
                )
        else:
            await update.callback_query.answer("❌ Ошибка при отключении пользователя")

    async def handle_show_all_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик показа всех пользователей"""
        if not user_manager.is_admin(update.effective_chat.id):
            await update.callback_query.answer("❌ У вас нет прав администратора")
            return

        users = user_manager.get_all_users()

        if not users:
            await update.callback_query.edit_message_text(
                "👥 Нет активных пользователей",
                parse_mode=ParseMode.HTML
            )
            return

        # Разбиваем пользователей на страницы по 15
        page_size = 15
        total_pages = (len(users) - 1) // page_size + 1

        text = f"👥 <b>Все пользователи ({len(users)}):</b>\n\n"

        keyboard = []

        for i, user in enumerate(users[:page_size]):  # Показываем первую страницу
            setup_status = "✅" if user.get('setup_completed', False) else "⚙️"
            watchlist_count = len(user.get('watchlist', []))
            last_activity = datetime.fromtimestamp(user['last_activity']).strftime('%d.%m %H:%M')

            text += (
                f"{i+1}. {setup_status} <b>{user['first_name']}</b> "
                f"(@{user.get('username', 'no_username')})\n"
                f"    • Монет: {watchlist_count} • Активность: {last_activity}\n"
            )

            # Создаем инлайн кнопку для отключения пользователя
            row = [
                InlineKeyboardButton(
                    f"🚫 Отключить {user['first_name']}", 
                    callback_data=f"revoke_{user['chat_id']}"
                )
            ]
            keyboard.append(row)

        if total_pages > 1:
            text += f"\n📄 Страница 1 из {total_pages}"

            # Добавляем кнопки навигации если больше одной страницы
            nav_row = []
            if total_pages > 1:
                nav_row.append(InlineKeyboardButton("➡️ Далее", callback_data="users_page_2"))
            keyboard.append(nav_row)

        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
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