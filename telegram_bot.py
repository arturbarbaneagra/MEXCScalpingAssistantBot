
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

*Доступные команды:*
• /start - главное меню
• /status - текущий статус  
• /help - справка

Бот автоматически отслеживает рынок и пришлет уведомления о торговых возможностях.
            """
            
            chat_id = config_manager.get('telegram_chat_id')
            if chat_id and self.application and self.application.bot:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=welcome_message,
                    parse_mode='Markdown'
                )
                
                self.bot_running = True
                self.bot_mode = "monitoring"  # Автоматически запускаем мониторинг
                bot_logger.info("🎉 Автоматическое приветственное сообщение отправлено, мониторинг активирован")
                
        except Exception as e:
            bot_logger.error(f"Ошибка отправки приветственного сообщения: {e}")
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        try:
            welcome_message = f"""
🤖 *Торговый бот v2.1*

Добро пожаловать! Бот готов к работе.

*Текущий статус:*
🤖 Статус: {'🟢 Работает' if self.bot_running else '🔴 Остановлен'}
📈 Режим: {self.bot_mode or 'Не установлен'}
💰 Активных монет: {len(self.active_coins)}
📋 Размер watchlist: {watchlist_manager.size()}

*Доступные команды:*
• /status - показать статус
• /help - справка по командам

Бот автоматически мониторит рынок и отправляет уведомления.
            """
            
            await update.message.reply_text(
                welcome_message, 
                parse_mode='Markdown'
            )
            
            if not self.bot_running:
                self.bot_running = True
                self.bot_mode = "monitoring"
                await update.message.reply_text("📊 *Мониторинг активирован!*", parse_mode='Markdown')
            
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
🆘 *Помощь по боту v2.1*

*Команды:*
• /start - Запуск бота и активация мониторинга
• /status - Показать текущий статус бота
• /help - Показать эту справку

*Что делает бот:*
• 📊 Автоматически мониторит {watchlist_manager.size()} монет
• 🔍 Ищет торговые возможности по объёму и волатильности
• ⚡ Отправляет уведомления о найденных сигналах
• 📈 Анализирует спреды и NATR показатели

*Настройки фильтров:*
• Минимальный объём: $1,500
• Минимальный спред: 0.1%
• Минимальный NATR: 0.4%

Бот работает полностью автоматически! 🚀
            """
            
            await update.message.reply_text(
                help_text,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            bot_logger.error(f"Ошибка в команде /help: {e}")
            await update.message.reply_text("Ошибка отображения справки.")
    
    

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
