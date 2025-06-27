
#!/usr/bin/env python3
"""
Telegram Bot для торгового бота
Версия: 2.1
"""

import asyncio
import time
import json
from datetime import datetime
from typing import Dict, Any, Optional, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

from logger import bot_logger
from config import config_manager
from watchlist_manager import watchlist_manager

class TradingTelegramBot:
    """Основной класс Telegram бота для торговли"""
    
    def __init__(self):
        self.bot_running = False
        self.bot_mode = None
        self.active_coins = {}
        self.monitoring_message_id = None
        self.application = None
        
    def setup_application(self) -> Application:
        """Настройка Telegram приложения"""
        try:
            token = config_manager.get('telegram_token')
            if not token:
                raise ValueError("TELEGRAM_TOKEN not found")
                
            self.application = Application.builder().token(token).build()
            
            # Добавляем обработчики команд
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("status", self.status_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            
            # Добавляем обработчик callback запросов
            self.application.add_handler(CallbackQueryHandler(self.callback_handler))
            
            bot_logger.info("Telegram приложение настроено")
            return self.application
            
        except Exception as e:
            bot_logger.error(f"Ошибка настройки Telegram приложения: {e}")
            raise
    
    async def send_startup_message(self):
        """Отправка приветственного сообщения при запуске"""
        try:
            welcome_message = """
👋 *Привет! Я тут!*

🤖 *Торговый бот v2.1* запущен и готов к работе!

Выберите действие:
            """
            
            # Создаем клавиатуру с кнопками
            keyboard = [
                [
                    InlineKeyboardButton("📊 Начать мониторинг", callback_data="start_monitoring"),
                    InlineKeyboardButton("📈 Статус", callback_data="show_status")
                ],
                [
                    InlineKeyboardButton("📋 Watchlist", callback_data="show_watchlist"),
                    InlineKeyboardButton("⚙️ Настройки", callback_data="show_settings")
                ],
                [
                    InlineKeyboardButton("🛑 Стоп", callback_data="stop_bot"),
                    InlineKeyboardButton("ℹ️ Помощь", callback_data="show_help")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            chat_id = config_manager.get('telegram_chat_id')
            if chat_id and self.application and self.application.bot:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=welcome_message,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
                
                self.bot_running = True
                bot_logger.info("🎉 Автоматическое приветственное сообщение отправлено")
                
        except Exception as e:
            bot_logger.error(f"Ошибка отправки приветственного сообщения: {e}")
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        try:
            welcome_message = """
🤖 *Торговый бот v2.1*

Добро пожаловать! Бот готов к работе.

Выберите действие:
            """
            
            # Создаем клавиатуру с кнопками
            keyboard = [
                [
                    InlineKeyboardButton("📊 Начать мониторинг", callback_data="start_monitoring"),
                    InlineKeyboardButton("📈 Статус", callback_data="show_status")
                ],
                [
                    InlineKeyboardButton("📋 Watchlist", callback_data="show_watchlist"),
                    InlineKeyboardButton("⚙️ Настройки", callback_data="show_settings")
                ],
                [
                    InlineKeyboardButton("🛑 Стоп", callback_data="stop_bot"),
                    InlineKeyboardButton("ℹ️ Помощь", callback_data="show_help")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                welcome_message, 
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            
            self.bot_running = True
            bot_logger.info(f"Бот активирован пользователем {update.effective_user.id}")
            
        except Exception as e:
            bot_logger.error(f"Ошибка в команде /start: {e}")
            await update.message.reply_text("Произошла ошибка при запуске бота.")
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /status"""
        try:
            status_info = f"""
📊 *Статус бота v2.1*

🤖 Статус: {'🟢 Работает' if self.bot_running else '🔴 Остановлен'}
📈 Режим: {self.bot_mode or 'Не установлен'}
💰 Активных монет: {len(self.active_coins)}
📋 Размер watchlist: {watchlist_manager.size()}
⏰ Время: {datetime.now().strftime('%H:%M:%S')}
            """
            
            await update.message.reply_text(
                status_info,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            bot_logger.error(f"Ошибка в команде /status: {e}")
            await update.message.reply_text("Ошибка получения статуса.")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /help"""
        try:
            help_text = """
🆘 *Помощь по боту*

*Команды:*
• /start - Запуск бота
• /status - Текущий статус
• /help - Эта справка

*Функции:*
• Автоматический мониторинг рынка
• Уведомления о торговых возможностях
• Анализ объемов и волатильности
• Отслеживание спредов

Бот работает автоматически после запуска.
            """
            
            await update.message.reply_text(
                help_text,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            bot_logger.error(f"Ошибка в команде /help: {e}")
            await update.message.reply_text("Ошибка отображения справки.")
    
    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик callback запросов от inline кнопок"""
        try:
            query = update.callback_query
            await query.answer()  # Убираем "loading" с кнопки
            
            data = query.data
            bot_logger.info(f"Получен callback: {data}")
            
            if data == "start_monitoring":
                await self.start_monitoring(query)
            elif data == "show_status":
                await self.show_status_callback(query)
            elif data == "show_watchlist":
                await self.show_watchlist_callback(query)
            elif data == "show_settings":
                await self.show_settings_callback(query)
            elif data == "stop_bot":
                await self.stop_bot_callback(query)
            elif data == "show_help":
                await self.show_help_callback(query)
            else:
                await query.edit_message_text("Неизвестная команда.")
                
        except Exception as e:
            bot_logger.error(f"Ошибка в callback handler: {e}")
            try:
                await query.edit_message_text("Произошла ошибка при обработке команды.")
            except:
                pass

    async def start_monitoring(self, query):
        """Запуск мониторинга"""
        self.bot_mode = "monitoring"
        message = """
📊 *Мониторинг запущен!*

🔍 Отслеживаю монеты из watchlist...
📈 Ищу торговые возможности
⚡ Уведомления будут приходить автоматически

Для остановки используйте кнопку "🛑 Стоп"
        """
        await query.edit_message_text(message, parse_mode='Markdown')
        bot_logger.info("Режим мониторинга активирован")

    async def show_status_callback(self, query):
        """Показать статус через callback"""
        status_info = f"""
📊 *Статус бота v2.1*

🤖 Статус: {'🟢 Работает' if self.bot_running else '🔴 Остановлен'}
📈 Режим: {self.bot_mode or 'Не установлен'}
💰 Активных монет: {len(self.active_coins)}
📋 Размер watchlist: {watchlist_manager.size()}
⏰ Время: {datetime.now().strftime('%H:%M:%S')}
        """
        await query.edit_message_text(status_info, parse_mode='Markdown')

    async def show_watchlist_callback(self, query):
        """Показать watchlist"""
        coins = watchlist_manager.get_symbols()
        if coins:
            coins_text = ", ".join(coins[:20])  # Показываем первые 20
            message = f"""
📋 *Watchlist ({len(coins)} монет)*

{coins_text}

{f"...и еще {len(coins) - 20} монет" if len(coins) > 20 else ""}
            """
        else:
            message = "📋 *Watchlist пуст*\n\nДобавьте монеты для отслеживания."
            
        await query.edit_message_text(message, parse_mode='Markdown')

    async def show_settings_callback(self, query):
        """Показать настройки"""
        volume_threshold = config_manager.get('VOLUME_THRESHOLD', 1500)
        spread_threshold = config_manager.get('SPREAD_THRESHOLD', 0.1)
        natr_threshold = config_manager.get('NATR_THRESHOLD', 0.4)
        
        settings_text = f"""
⚙️ *Текущие настройки*

📊 Минимальный объём: ${volume_threshold:,.0f}
⇄ Минимальный спред: {spread_threshold:.1%}
📈 Минимальный NATR: {natr_threshold:.1%}

Настройки можно изменить в config.json
        """
        await query.edit_message_text(settings_text, parse_mode='Markdown')

    async def stop_bot_callback(self, query):
        """Остановить бота"""
        self.bot_mode = None
        self.active_coins.clear()
        message = """
🛑 *Бот остановлен*

📊 Мониторинг отключен
💰 Активные монеты очищены
⏸️ Режим ожидания

Для запуска используйте /start
        """
        await query.edit_message_text(message, parse_mode='Markdown')
        bot_logger.info("Бот остановлен пользователем")

    async def show_help_callback(self, query):
        """Показать помощь"""
        help_text = """
🆘 *Помощь по боту v2.1*

*Кнопки:*
📊 Начать мониторинг - запустить отслеживание
📈 Статус - текущее состояние бота
📋 Watchlist - список отслеживаемых монет
⚙️ Настройки - параметры фильтров
🛑 Стоп - остановить мониторинг
ℹ️ Помощь - эта справка

*Команды:*
/start - главное меню
/status - быстрый статус
/help - справка

Бот автоматически ищет торговые возможности!
        """
        await query.edit_message_text(help_text, parse_mode='Markdown')

    async def send_notification(self, message: str, parse_mode: str = 'Markdown'):
        """Отправка уведомления в Telegram"""
        try:
            if not self.application or not self.application.bot:
                bot_logger.warning("Telegram приложение не инициализировано")
                return False
                
            chat_id = config_manager.get('telegram_chat_id')
            if not chat_id:
                bot_logger.error("TELEGRAM_CHAT_ID не найден")
                return False
                
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=parse_mode
            )
            
            return True
            
        except Exception as e:
            bot_logger.error(f"Ошибка отправки уведомления: {e}")
            return False

# Создаем глобальный экземпляр
telegram_bot = TradingTelegramBot()
