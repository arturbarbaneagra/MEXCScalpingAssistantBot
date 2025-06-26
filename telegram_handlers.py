
from typing import Optional
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode
from logger import bot_logger
from config import config_manager
from api_client import api_client
from watchlist_manager import watchlist_manager
from bot_state import bot_state_manager

class TelegramHandlers:
    def __init__(self, bot_instance):
        self.bot = bot_instance

    async def start_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        # Проверяем, нужно ли восстановить последний режим
        last_mode = bot_state_manager.get_last_mode()

        welcome_text = (
            "🤖 <b>Добро пожаловать в торговый бот v2.1!</b>\n\n"
            "📊 <b>Режимы работы:</b>\n"
            "• 🔔 <b>Уведомления</b> - оповещения об активных монетах\n"
            "• 📊 <b>Мониторинг</b> - постоянное отслеживание списка\n\n"
            "⚙ <b>Управление:</b>\n"
            "• ➕ Добавить монету в список\n"
            "• ➖ Удалить монету из списка\n"
            "• 📋 Показать список монет\n"
            "• ⚙ Настройки фильтров\n\n"
        )

        # Автовосстановление последнего режима
        if last_mode and not self.bot.bot_running:
            if last_mode == 'notification':
                welcome_text += "🔄 <b>Восстанавливаю режим уведомлений...</b>\n\n"
                await update.message.reply_text(
                    welcome_text + "Выберите действие:", 
                    reply_markup=self.bot.main_keyboard, 
                    parse_mode=ParseMode.HTML
                )

                self.bot.bot_mode = 'notification'
                self.bot.bot_running = True
                self.bot.start_monitoring_loop()

                await self.bot.send_message(
                    "✅ <b>Режим уведомлений восстановлен</b>\n"
                    "Вы будете получать уведомления об активных монетах."
                )
                return

            elif last_mode == 'monitoring':
                welcome_text += "🔄 <b>Восстанавливаю режим мониторинга...</b>\n\n"
                await update.message.reply_text(
                    welcome_text + "Выберите действие:", 
                    reply_markup=self.bot.main_keyboard, 
                    parse_mode=ParseMode.HTML
                )

                self.bot.bot_mode = 'monitoring'
                self.bot.bot_running = True
                self.bot.start_monitoring_loop()

                await self.bot.send_message(
                    "✅ <b>Режим мониторинга восстановлен</b>\n"
                    "Сводка будет обновляться автоматически."
                )
                return

        welcome_text += "Выберите действие:"
        await update.message.reply_text(
            welcome_text, 
            reply_markup=self.bot.main_keyboard, 
            parse_mode=ParseMode.HTML
        )

    async def add_coin_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик добавления монеты"""
        text = update.message.text.strip()

        if text == "🔙 Назад":
            await self._handle_back(update)
            return ConversationHandler.END

        # Нормализуем символ
        symbol = text.upper().replace("_USDT", "").replace("USDT", "")

        if not symbol or len(symbol) < 2:
            await update.message.reply_text(
                "❌ Некорректный символ. Попробуйте еще раз:",
                reply_markup=self.bot.back_keyboard
            )
            return self.bot.ADDING_COIN

        # Проверяем, есть ли уже в списке
        if watchlist_manager.contains(symbol):
            await update.message.reply_text(
                f"⚠ <b>{symbol}</b> уже в списке отслеживания.",
                reply_markup=self.bot.main_keyboard,
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END

        # Проверяем доступность монеты
        await update.message.reply_text("🔄 Проверяю доступность монеты...")

        try:
            coin_data = await api_client.get_coin_data(symbol)

            if coin_data:
                watchlist_manager.add(symbol)
                await update.message.reply_text(
                    f"✅ <b>{symbol}_USDT</b> добавлена в список отслеживания\n"
                    f"💰 Текущая цена: ${coin_data['price']:.6f}\n"
                    f"📊 1м объём: ${coin_data['volume']:,.2f}\n"
                    f"🔄 1м изменение: {coin_data['change']:+.2f}%\n"
                    f"⇄ Спред: {coin_data['spread']:.2f}%",
                    reply_markup=self.bot.main_keyboard,
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    f"❌ <b>{symbol}_USDT</b> не найдена или недоступна для торговли.",
                    reply_markup=self.bot.main_keyboard,
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            bot_logger.error(f"Ошибка при добавлении монеты {symbol}: {e}")
            await update.message.reply_text(
                f"❌ Ошибка при проверке <b>{symbol}</b>. Попробуйте позже.",
                reply_markup=self.bot.main_keyboard,
                parse_mode=ParseMode.HTML
            )

        return ConversationHandler.END

    async def _handle_back(self, update: Update):
        """Возврат в главное меню"""
        await update.message.reply_text(
            "🏠 Главное меню:",
            reply_markup=self.bot.main_keyboard
        )
