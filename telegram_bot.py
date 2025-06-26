
import asyncio
import time
import threading
from typing import Dict, Optional, List
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, ConversationHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from logger import bot_logger
from config import config_manager
from api_client import api_client
from watchlist_manager import watchlist_manager
from bot_state import bot_state_manager
import os

class TradingTelegramBot:
    def __init__(self):
        self.token = os.getenv('TELEGRAM_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')

        if not self.token or not self.chat_id:
            raise ValueError("TELEGRAM_TOKEN и TELEGRAM_CHAT_ID должны быть установлены в переменных окружения")

        self.app = None
        self.bot_running = False
        self.bot_mode = None
        self.active_coins: Dict[str, Dict] = {}
        self.monitoring_message_id = None
        self.last_message_time = 0

        # Состояния ConversationHandler
        self.ADDING_COIN, self.REMOVING_COIN = range(2)
        self.SETTING_VOLUME, self.SETTING_SPREAD, self.SETTING_NATR = range(2, 5)

        self._setup_keyboards()

    def _setup_keyboards(self):
        """Настраивает клавиатуры"""
        self.main_keyboard = ReplyKeyboardMarkup([
            ["🔔 Уведомления", "📊 Мониторинг"],
            ["➕ Добавить", "➖ Удалить"],
            ["📋 Список", "⚙ Настройки"],
            ["🛑 Стоп", "ℹ Статус"]
        ], resize_keyboard=True, one_time_keyboard=False)

        self.settings_keyboard = ReplyKeyboardMarkup([
            ["📊 Объём", "⇄ Спред"],
            ["📈 NATR", "🔄 Сброс"],
            ["🔙 Назад"]
        ], resize_keyboard=True)

        self.back_keyboard = ReplyKeyboardMarkup([
            ["🔙 Назад"]
        ], resize_keyboard=True)

    async def _rate_limit_message(self):
        """Ограничение частоты отправки сообщений"""
        current_time = time.time()
        min_interval = config_manager.get('MESSAGE_RATE_LIMIT')

        if current_time - self.last_message_time < min_interval:
            await asyncio.sleep(min_interval - (current_time - self.last_message_time))

        self.last_message_time = time.time()

    async def send_message(self, text: str, reply_markup=None, parse_mode=ParseMode.HTML) -> Optional[int]:
        """Отправляет сообщение с ограничением частоты"""
        if not self.app or not self.app.bot:
            bot_logger.error("Бот не инициализирован для отправки сообщения")
            return None

        await self._rate_limit_message()

        try:
            message = await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
            return message.message_id
        except Exception as e:
            bot_logger.error(f"Ошибка отправки сообщения: {e}")
            return None

    async def edit_message(self, message_id: int, text: str, reply_markup=None):
        """Редактирует сообщение"""
        try:
            await self.app.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            bot_logger.error(f"Ошибка редактирования сообщения: {e}")

    async def delete_message(self, message_id: int) -> bool:
        """Удаляет сообщение"""
        if not message_id or not isinstance(message_id, int) or message_id <= 0:
            bot_logger.debug(f"Некорректный ID сообщения: {message_id}")
            return False
            
        if not self.app or not self.app.bot:
            bot_logger.debug(f"Приложение или бот не инициализированы для удаления сообщения {message_id}")
            return False

        try:
            await self.app.bot.delete_message(chat_id=self.chat_id, message_id=message_id)
            bot_logger.debug(f"Сообщение {message_id} успешно удалено")
            return True
        except Exception as e:
            error_message = str(e).lower()
            # Игнорируем обычные ошибки удаления
            if any(phrase in error_message for phrase in [
                "message to delete not found",
                "message can't be deleted", 
                "message is too old",
                "bad request",
                "not found"
            ]):
                bot_logger.debug(f"Сообщение {message_id} недоступно для удаления: {e}")
            else:
                bot_logger.error(f"Ошибка удаления сообщения {message_id}: {e}")
            return False

    def _chunks(self, lst: List, size: int):
        """Разбивает список на чанки"""
        for i in range(0, len(lst), size):
            yield lst[i:i + size]

    async def _stop_current_mode(self):
        """Останавливает текущий режим работы бота"""
        if self.bot_mode:
            bot_logger.info(f"🛑 Остановка режима: {self.bot_mode}")

            # Сначала останавливаем циклы
            self.bot_running = False
            
            # Даем время циклам завершиться
            await asyncio.sleep(0.5)

            # Удаляем сообщение мониторинга если оно есть
            if self.monitoring_message_id:
                success = await self.delete_message(self.monitoring_message_id)
                if success:
                    bot_logger.info("📝 Сообщение мониторинга удалено")
                self.monitoring_message_id = None

            # Удаляем активные уведомления
            if self.active_coins:
                deleted_count = 0
                for symbol, coin_data in list(self.active_coins.items()):
                    if coin_data.get('msg_id'):
                        success = await self.delete_message(coin_data['msg_id'])
                        if success:
                            deleted_count += 1
                if deleted_count > 0:
                    bot_logger.info(f"🗑 Удалено {deleted_count} уведомлений")
                self.active_coins.clear()

            self.bot_mode = None

            # Сохраняем состояние
            bot_state_manager.set_last_mode(None)

    async def _notification_mode_loop(self):
        """Цикл режима уведомлений"""
        bot_logger.info("Запущен режим уведомлений")

        while self.bot_running and self.bot_mode == 'notification':
            watchlist = watchlist_manager.get_all()
            if not watchlist:
                await asyncio.sleep(config_manager.get('CHECK_FULL_CYCLE_INTERVAL'))
                continue

            batch_size = config_manager.get('CHECK_BATCH_SIZE')
            for batch in self._chunks(list(watchlist), batch_size):
                if not self.bot_running or self.bot_mode != 'notification':
                    break

                # Получаем данные параллельно
                tasks = []
                for symbol in batch:
                    task = asyncio.create_task(
                        asyncio.to_thread(api_client.get_coin_data, symbol)
                    )
                    tasks.append((symbol, task))

                for symbol, task in tasks:
                    try:
                        data = await task
                        if data:
                            await self._process_coin_notification(symbol, data)
                    except Exception as e:
                        bot_logger.error(f"Ошибка обработки {symbol}: {e}")

                await asyncio.sleep(config_manager.get('CHECK_BATCH_INTERVAL'))

            try:
                await asyncio.sleep(config_manager.get('CHECK_FULL_CYCLE_INTERVAL'))
            except Exception as e:
                bot_logger.error(f"Ошибка в цикле уведомлений: {e}")
                await asyncio.sleep(5)  # Пауза при ошибке

    async def _process_coin_notification(self, symbol: str, data: Dict):
        """Обрабатывает уведомление для монеты"""
        now = time.time()
        is_currently_active = symbol in self.active_coins

        if data['active']:
            if not is_currently_active:
                # Новая активная монета
                message = self._format_coin_message(data, "🚨 АКТИВНОСТЬ")
                msg_id = await self.send_message(message)

                if msg_id:
                    self.active_coins[symbol] = {
                        'start_time': now,
                        'last_active': now,
                        'msg_id': msg_id,
                        'data': data
                    }
                    bot_logger.trade_activity(symbol, "STARTED", f"Volume: ${data['volume']:,.2f}")
            else:
                # Обновляем существующую активную монету
                self.active_coins[symbol]['last_active'] = now
                self.active_coins[symbol]['data'] = data

                message = self._format_coin_message(data, "🚨 АКТИВНОСТЬ")
                await self.edit_message(self.active_coins[symbol]['msg_id'], message)

        elif is_currently_active:
            # Проверяем таймаут неактивности
            inactive_time = now - self.active_coins[symbol]['last_active']
            if inactive_time > config_manager.get('INACTIVITY_TIMEOUT'):
                await self._end_coin_activity(symbol, now)

    async def _end_coin_activity(self, symbol: str, end_time: float):
        """Завершает активность монеты"""
        coin_info = self.active_coins[symbol]
        duration = end_time - coin_info['start_time']

        # Удаляем сообщение об активности
        if coin_info['msg_id']:
            await self.delete_message(coin_info['msg_id'])

        # Отправляем сообщение о завершении (если активность была достаточно долгой)
        if duration >= 60:
            minutes = int(duration // 60)
            seconds = int(duration % 60)
            end_message = (
                f"✅ <b>{symbol}_USDT завершил активность</b>\n"
                f"⏱ Длительность: {minutes}м {seconds}с"
            )
            await self.send_message(end_message)
            bot_logger.trade_activity(symbol, "ENDED", f"Duration: {minutes}m {seconds}s")

        del self.active_coins[symbol]

    def _format_coin_message(self, data: Dict, status: str) -> str:
        """Форматирует сообщение о монете"""
        return (
            f"{status} <b>{data['symbol']}_USDT</b>\n"
            f"💰 Цена: ${data['price']:.6f}\n"
            f"🔄 1м изменение: {data['change']:+.2f}%\n"
            f"📊 1м объём: ${data['volume']:,.2f}\n"
            f"📈 NATR: {data['natr']:.2f}%\n"
            f"⇄ Спред: {data['spread']:.2f}%\n"
            f"🔁 1м сделок: {data['trades']}"
        )

    async def _monitoring_mode_loop(self):
        """Цикл режима мониторинга"""
        bot_logger.info("Запущен режим мониторинга")

        # Отправляем начальное сообщение
        initial_text = "🔄 <b>Инициализация мониторинга...</b>"
        self.monitoring_message_id = await self.send_message(initial_text)

        while self.bot_running and self.bot_mode == 'monitoring':
            watchlist = watchlist_manager.get_all()
            if not watchlist:
                no_coins_text = "❌ <b>Список отслеживания пуст</b>\nДобавьте монеты для мониторинга."
                if self.monitoring_message_id:
                    await self.edit_message(self.monitoring_message_id, no_coins_text)
                await asyncio.sleep(config_manager.get('MONITORING_UPDATE_INTERVAL'))
                continue

            results = []
            failed_coins = []

            # Получаем данные по всем монетам
            batch_size = config_manager.get('CHECK_BATCH_SIZE')
            for batch in self._chunks(sorted(watchlist), batch_size):
                if not self.bot_running or self.bot_mode != 'monitoring':
                    break

                batch_tasks = []
                for symbol in batch:
                    task = asyncio.create_task(
                        asyncio.to_thread(api_client.get_coin_data, symbol)
                    )
                    batch_tasks.append((symbol, task))

                for symbol, task in batch_tasks:
                    try:
                        data = await task
                        if data:
                            results.append(data)
                        else:
                            failed_coins.append(symbol)
                    except Exception as e:
                        bot_logger.error(f"Ошибка получения данных для {symbol}: {e}")
                        failed_coins.append(symbol)

                await asyncio.sleep(config_manager.get('CHECK_BATCH_INTERVAL'))

            # Формируем отчет
            if results:
                report = self._format_monitoring_report(results, failed_coins)
                if self.monitoring_message_id:
                    await self.edit_message(self.monitoring_message_id, report)
                else:
                    self.monitoring_message_id = await self.send_message(report)

            await asyncio.sleep(config_manager.get('MONITORING_UPDATE_INTERVAL'))

        # Очищаем при остановке режима мониторинга
        if self.monitoring_message_id:
            bot_logger.info(f"Режим мониторинга завершен, удаляем сообщение: {self.monitoring_message_id}")
            await self.delete_message(self.monitoring_message_id)
            self.monitoring_message_id = None

    def _format_monitoring_report(self, results: List[Dict], failed_coins: List[str]) -> str:
        """Форматирует отчет мониторинга"""
        # Сортируем по объему
        results.sort(key=lambda x: x['volume'], reverse=True)

        parts = ["<b>📊 Скальпинг мониторинг (1м данные)</b>\n"]

        # Информация о фильтрах
        vol_thresh = config_manager.get('VOLUME_THRESHOLD')
        spread_thresh = config_manager.get('SPREAD_THRESHOLD')
        natr_thresh = config_manager.get('NATR_THRESHOLD')

        parts.append(
            f"<i>1м фильтры: Объём ≥${vol_thresh:,}, "
            f"Спред ≥{spread_thresh}%, NATR ≥{natr_thresh}%</i>\n"
        )

        if failed_coins:
            parts.append(f"⚠ <i>Ошибки: {', '.join(failed_coins[:5])}</i>\n")

        # Показываем активные монеты
        active_coins = [r for r in results if r['active']]
        if active_coins:
            parts.append("<b>🟢 АКТИВНЫЕ:</b>")
            for coin in active_coins[:10]:  # Показываем только первые 10
                parts.append(
                    f"• <b>{coin['symbol']}</b> "
                    f"${coin['volume']:,.0f} | {coin['change']:+.1f}% | "
                    f"S:{coin['spread']:.2f}% | N:{coin['natr']:.2f}%"
                )
            parts.append("")

        # Показываем неактивные монеты (топ по объему)
        inactive_coins = [r for r in results if not r['active']]
        if inactive_coins:
            parts.append("<b>🔴 НЕАКТИВНЫЕ (топ по объёму):</b>")
            for coin in inactive_coins[:8]:  # Показываем больше неактивных
                parts.append(
                    f"• <b>{coin['symbol']}</b> "
                    f"${coin['volume']:,.0f} | {coin['change']:+.1f}% | "
                    f"S:{coin['spread']:.2f}% | N:{coin['natr']:.2f}%"
                )

        # Добавляем статистику
        parts.append(f"\n📈 Активных: {len(active_coins)}/{len(results)}")

        report = "\n".join(parts)

        # Обрезаем, если слишком длинное
        if len(report) > 4000:
            report = report[:4000] + "\n... <i>(отчет обрезан)</i>"

        return report

    def start_monitoring_loop(self):
        """Запускает цикл мониторинга в отдельном потоке"""
        def run_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            if self.bot_mode == 'notification':
                loop.run_until_complete(self._notification_mode_loop())
            elif self.bot_mode == 'monitoring':
                loop.run_until_complete(self._monitoring_mode_loop())

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()
        return thread

    # Telegram Handlers
    async def start_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        # Проверяем, нужно ли восстановить последний режим
        last_mode = bot_state_manager.get_last_mode()

        welcome_text = (
            "🤖 <b>Добро пожаловать в торговый бот!</b>\n\n"
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
        if last_mode and not self.bot_running:
            if last_mode == 'notification':
                welcome_text += "🔄 <b>Восстанавливаю режим уведомлений...</b>\n\n"
                await update.message.reply_text(welcome_text + "Выберите действие:", reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML)

                self.bot_mode = 'notification'
                self.bot_running = True
                self.start_monitoring_loop()

                await self.send_message(
                    "✅ <b>Режим уведомлений восстановлен</b>\n"
                    "Вы будете получать уведомления об активных монетах."
                )
                return

            elif last_mode == 'monitoring':
                welcome_text += "🔄 <b>Восстанавливаю режим мониторинга...</b>\n\n"
                await update.message.reply_text(welcome_text + "Выберите действие:", reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML)

                self.bot_mode = 'monitoring'
                self.bot_running = True
                self.start_monitoring_loop()

                await self.send_message(
                    "✅ <b>Режим мониторинга восстановлен</b>\n"
                    "Сводка будет обновляться автоматически."
                )
                return

        welcome_text += "Выберите действие:"
        await update.message.reply_text(welcome_text, reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML)

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Основной обработчик кнопок"""
        text = update.message.text

        try:
            if text == "🔔 Уведомления":
                await self._handle_notification_mode(update)
            elif text == "📊 Мониторинг":
                await self._handle_monitoring_mode(update)
            elif text == "🛑 Стоп":
                await self._handle_stop(update)
            elif text == "➕ Добавить":
                return await self._handle_add_coin_start(update)
            elif text == "➖ Удалить":
                return await self._handle_remove_coin_start(update)
            elif text == "📋 Список":
                await self._handle_show_list(update)
            elif text == "⚙ Настройки":
                await self._handle_settings(update)
            elif text == "📊 Объём":
                return await self._handle_volume_setting_start(update)
            elif text == "⇄ Спред":
                return await self._handle_spread_setting_start(update)
            elif text == "📈 NATR":
                return await self._handle_natr_setting_start(update)
            elif text == "🔄 Сброс":
                await self._handle_reset_settings(update)
            elif text == "ℹ Статус":
                await self._handle_status(update)
            elif text == "🔙 Назад":
                await self._handle_back(update)
            else:
                await update.message.reply_text(
                    "❓ Неизвестная команда. Используйте кнопки меню.",
                    reply_markup=self.main_keyboard
                )
        except Exception as e:
            bot_logger.error(f"Ошибка в button_handler: {e}", exc_info=True)
            await update.message.reply_text(
                "❌ Произошла ошибка. Попробуйте еще раз.",
                reply_markup=self.main_keyboard
            )

        return ConversationHandler.END

    async def _handle_notification_mode(self, update: Update):
        """Обработка режима уведомлений"""
        if self.bot_running and self.bot_mode == 'notification':
            await update.message.reply_text(
                "✅ Бот уже работает в режиме уведомлений.",
                reply_markup=self.main_keyboard
            )
            return

        # Останавливаем текущий режим (включая удаление сообщений)
        await self._stop_current_mode()

        # Небольшая задержка для стабилизации
        await asyncio.sleep(0.3)

        # Дополнительно очищаем сообщение мониторинга, если оно есть
        if self.monitoring_message_id:
            bot_logger.info(f"Принудительная очистка сообщения мониторинга: {self.monitoring_message_id}")
            await self.delete_message(self.monitoring_message_id)
            self.monitoring_message_id = None

        # Запускаем новый режим
        self.bot_mode = 'notification'
        self.bot_running = True
        bot_state_manager.set_last_mode('notification')
        self.start_monitoring_loop()

        await update.message.reply_text(
            "✅ <b>Режим уведомлений активирован</b>\n"
            "Вы будете получать уведомления об активных монетах.",
            reply_markup=self.main_keyboard,
            parse_mode=ParseMode.HTML
        )

    async def _handle_monitoring_mode(self, update: Update):
        """Обработка режима мониторинга"""
        if self.bot_running and self.bot_mode == 'monitoring':
            await update.message.reply_text(
                "✅ Бот уже работает в режиме мониторинга.",
                reply_markup=self.main_keyboard
            )
            return

        # Останавливаем текущий режим (включая удаление сообщений)
        await self._stop_current_mode()

        # Небольшая задержка для стабилизации
        await asyncio.sleep(0.3)

        # Дополнительно очищаем активные уведомления, если они есть
        if self.active_coins:
            for symbol, coin_data in list(self.active_coins.items()):
                if coin_data.get('msg_id'):
                    bot_logger.info(f"Принудительная очистка уведомления для {symbol}: {coin_data['msg_id']}")
                    await self.delete_message(coin_data['msg_id'])
            self.active_coins.clear()

        # Запускаем новый режим
        self.bot_mode = 'monitoring'
        self.bot_running = True
        bot_state_manager.set_last_mode('monitoring')
        self.start_monitoring_loop()

        await update.message.reply_text(
            "✅ <b>Режим мониторинга активирован</b>\n"
            "Сводка будет обновляться автоматически.",
            reply_markup=self.main_keyboard,
            parse_mode=ParseMode.HTML
        )

    async def _handle_stop(self, update: Update):
        """Обработка остановки бота"""
        await self._stop_current_mode()
        await update.message.reply_text(
            "🛑 <b>Бот остановлен</b>",
            reply_markup=self.main_keyboard,
            parse_mode=ParseMode.HTML
        )

    async def _handle_add_coin_start(self, update: Update):
        """Начало добавления монеты"""
        await self._stop_current_mode()
        await update.message.reply_text(
            "➕ <b>Добавление монеты</b>\n\n"
            "Введите символ монеты (например: <code>BTC</code> или <code>BTC_USDT</code>):",
            reply_markup=self.back_keyboard,
            parse_mode=ParseMode.HTML
        )
        return self.ADDING_COIN

    async def _handle_remove_coin_start(self, update: Update):
        """Начало удаления монеты"""
        await self._stop_current_mode()

        if watchlist_manager.size() == 0:
            await update.message.reply_text(
                "❌ Список отслеживания пуст.",
                reply_markup=self.main_keyboard
            )
            return ConversationHandler.END

        coins_list = ", ".join(sorted(watchlist_manager.get_all())[:10])
        if watchlist_manager.size() > 10:
            coins_list += "..."

        await update.message.reply_text(
            f"➖ <b>Удаление монеты</b>\n\n"
            f"Текущий список: {coins_list}\n\n"
            f"Введите символ монеты для удаления:",
            reply_markup=self.back_keyboard,
            parse_mode=ParseMode.HTML
        )
        return self.REMOVING_COIN

    async def _handle_volume_setting_start(self, update: Update):
        """Начало настройки объёма"""
        await self._stop_current_mode()
        current_value = config_manager.get('VOLUME_THRESHOLD')
        await update.message.reply_text(
            f"📊 <b>Настройка минимального объёма</b>\n\n"
            f"Текущее значение: <code>${current_value:,}</code>\n\n"
            f"Введите новое значение в долларах (например: 1500):",
            reply_markup=self.back_keyboard,
            parse_mode=ParseMode.HTML
        )
        return self.SETTING_VOLUME

    async def _handle_spread_setting_start(self, update: Update):
        """Начало настройки спреда"""
        await self._stop_current_mode()
        current_value = config_manager.get('SPREAD_THRESHOLD')
        await update.message.reply_text(
            f"⇄ <b>Настройка минимального спреда</b>\n\n"
            f"Текущее значение: <code>{current_value}%</code>\n\n"
            f"Введите новое значение в процентах (например: 0.2):",
            reply_markup=self.back_keyboard,
            parse_mode=ParseMode.HTML
        )
        return self.SETTING_SPREAD

    async def _handle_natr_setting_start(self, update: Update):
        """Начало настройки NATR"""
        await self._stop_current_mode()
        current_value = config_manager.get('NATR_THRESHOLD')
        await update.message.reply_text(
            f"📈 <b>Настройка минимального NATR</b>\n\n"
            f"Текущее значение: <code>{current_value}%</code>\n\n"
            f"Введите новое значение в процентах (например: 0.8):",
            reply_markup=self.back_keyboard,
            parse_mode=ParseMode.HTML
        )
        return self.SETTING_NATR

    async def _handle_show_list(self, update: Update):
        """Показ списка монет"""
        await self._stop_current_mode()

        coins = watchlist_manager.get_all()
        if not coins:
            text = "📋 <b>Список отслеживания пуст</b>"
        else:
            sorted_coins = sorted(coins)
            text = f"📋 <b>Список отслеживания ({len(coins)} монет):</b>\n\n"

            # Разбиваем на строки по 5 монет
            for i in range(0, len(sorted_coins), 5):
                batch = sorted_coins[i:i+5]
                text += " • ".join(batch) + "\n"

        await update.message.reply_text(text, reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML)

    async def _handle_settings(self, update: Update):
        """Обработка настроек"""
        await self._stop_current_mode()

        current_settings = (
            "⚙ <b>Текущие настройки фильтров:</b>\n\n"
            f"📊 Минимальный объём: <code>${config_manager.get('VOLUME_THRESHOLD'):,}</code>\n"
            f"⇄ Минимальный спред: <code>{config_manager.get('SPREAD_THRESHOLD')}%</code>\n"
            f"📈 Минимальный NATR: <code>{config_manager.get('NATR_THRESHOLD')}%</code>\n\n"
            "Выберите параметр для изменения:"
        )

        await update.message.reply_text(
            current_settings,
            reply_markup=self.settings_keyboard,
            parse_mode=ParseMode.HTML
        )

    async def _handle_status(self, update: Update):
        """Показ статуса бота"""
        status_parts = ["ℹ <b>Статус бота:</b>\n"]

        if self.bot_running:
            status_parts.append(f"🟢 Работает в режиме: <b>{self.bot_mode}</b>")
            if self.bot_mode == 'notification':
                status_parts.append(f"📊 Активных монет: <b>{len(self.active_coins)}</b>")
        else:
            status_parts.append("🔴 Остановлен")

        status_parts.append(f"📋 Монет в списке: <b>{watchlist_manager.size()}</b>")

        # Показываем текущие фильтры
        status_parts.append("\n⚙ <b>Фильтры:</b>")
        status_parts.append(f"• Объём: ${config_manager.get('VOLUME_THRESHOLD'):,}")
        status_parts.append(f"• Спред: {config_manager.get('SPREAD_THRESHOLD')}%")
        status_parts.append(f"• NATR: {config_manager.get('NATR_THRESHOLD')}%")

        await update.message.reply_text(
            "\n".join(status_parts),
            reply_markup=self.main_keyboard,
            parse_mode=ParseMode.HTML
        )

    async def _handle_reset_settings(self, update: Update):
        """Сброс настроек к значениям по умолчанию"""
        await self._stop_current_mode()
        
        # Сбрасываем к значениям по умолчанию
        config_manager.set('VOLUME_THRESHOLD', 1000)
        config_manager.set('SPREAD_THRESHOLD', 0.1)
        config_manager.set('NATR_THRESHOLD', 0.5)
        
        reset_message = (
            "🔄 <b>Настройки сброшены к значениям по умолчанию:</b>\n\n"
            f"📊 Минимальный объём: <code>$1,000</code>\n"
            f"⇄ Минимальный спред: <code>0.1%</code>\n"
            f"📈 Минимальный NATR: <code>0.5%</code>"
        )
        
        await update.message.reply_text(
            reset_message,
            reply_markup=self.main_keyboard,
            parse_mode=ParseMode.HTML
        )

    async def _handle_back(self, update: Update):
        """Возврат в главное меню"""
        await update.message.reply_text(
            "🏠 Главное меню:",
            reply_markup=self.main_keyboard
        )

    # Handlers для ConversationHandler
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
                reply_markup=self.back_keyboard
            )
            return self.ADDING_COIN

        # Проверяем, есть ли уже в списке
        if watchlist_manager.contains(symbol):
            await update.message.reply_text(
                f"⚠ <b>{symbol}</b> уже в списке отслеживания.",
                reply_markup=self.main_keyboard,
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END

        # Проверяем доступность монеты
        await update.message.reply_text("🔄 Проверяю доступность монеты...")

        try:
            coin_data = await asyncio.to_thread(api_client.get_coin_data, symbol)

            if coin_data:
                watchlist_manager.add(symbol)
                await update.message.reply_text(
                    f"✅ <b>{symbol}_USDT</b> добавлена в список отслеживания\n"
                    f"💰 Текущая цена: ${coin_data['price']:.6f}\n"
                    f"📊 Объём: ${coin_data['volume']:,.2f}",
                    reply_markup=self.main_keyboard,
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    f"❌ <b>{symbol}_USDT</b> не найдена или недоступна для торговли.",
                    reply_markup=self.main_keyboard,
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            bot_logger.error(f"Ошибка при добавлении монеты {symbol}: {e}")
            await update.message.reply_text(
                f"❌ Ошибка при проверке <b>{symbol}</b>. Попробуйте позже.",
                reply_markup=self.main_keyboard,
                parse_mode=ParseMode.HTML
            )

        return ConversationHandler.END

    async def remove_coin_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик удаления монеты"""
        text = update.message.text.strip()

        if text == "🔙 Назад":
            await self._handle_back(update)
            return ConversationHandler.END

        symbol = text.upper().replace("_USDT", "").replace("USDT", "")

        if watchlist_manager.remove(symbol):
            await update.message.reply_text(
                f"✅ <b>{symbol}</b> удалена из списка отслеживания.",
                reply_markup=self.main_keyboard,
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                f"❌ <b>{symbol}</b> не найдена в списке.",
                reply_markup=self.main_keyboard,
                parse_mode=ParseMode.HTML
            )

        return ConversationHandler.END

    async def volume_setting_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик настройки объёма"""
        text = update.message.text.strip()

        if text == "🔙 Назад":
            await self._handle_settings(update)
            return ConversationHandler.END

        try:
            value = int(text)
            if value < 100:
                await update.message.reply_text(
                    "❌ Объём должен быть не менее $100. Попробуйте еще раз:",
                    reply_markup=self.back_keyboard
                )
                return self.SETTING_VOLUME

            config_manager.set('VOLUME_THRESHOLD', value)
            await update.message.reply_text(
                f"✅ <b>Минимальный объём установлен:</b> ${value:,}",
                reply_markup=self.main_keyboard,
                parse_mode=ParseMode.HTML
            )
        except ValueError:
            await update.message.reply_text(
                "❌ Введите числовое значение. Попробуйте еще раз:",
                reply_markup=self.back_keyboard
            )
            return self.SETTING_VOLUME

        return ConversationHandler.END

    async def spread_setting_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик настройки спреда"""
        text = update.message.text.strip()

        if text == "🔙 Назад":
            await self._handle_settings(update)
            return ConversationHandler.END

        try:
            value = float(text)
            if value < 0 or value > 10:
                await update.message.reply_text(
                    "❌ Спред должен быть от 0 до 10%. Попробуйте еще раз:",
                    reply_markup=self.back_keyboard
                )
                return self.SETTING_SPREAD

            config_manager.set('SPREAD_THRESHOLD', value)
            await update.message.reply_text(
                f"✅ <b>Минимальный спред установлен:</b> {value}%",
                reply_markup=self.main_keyboard,
                parse_mode=ParseMode.HTML
            )
        except ValueError:
            await update.message.reply_text(
                "❌ Введите числовое значение. Попробуйте еще раз:",
                reply_markup=self.back_keyboard
            )
            return self.SETTING_SPREAD

        return ConversationHandler.END

    async def natr_setting_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик настройки NATR"""
        text = update.message.text.strip()

        if text == "🔙 Назад":
            await self._handle_settings(update)
            return ConversationHandler.END

        try:
            value = float(text)
            if value < 0 or value > 20:
                await update.message.reply_text(
                    "❌ NATR должен быть от 0 до 20%. Попробуйте еще раз:",
                    reply_markup=self.back_keyboard
                )
                return self.SETTING_NATR

            config_manager.set('NATR_THRESHOLD', value)
            await update.message.reply_text(
                f"✅ <b>Минимальный NATR установлен:</b> {value}%",
                reply_markup=self.main_keyboard,
                parse_mode=ParseMode.HTML
            )
        except ValueError:
            await update.message.reply_text(
                "❌ Введите числовое значение. Попробуйте еще раз:",
                reply_markup=self.back_keyboard
            )
            return self.SETTING_NATR

        return ConversationHandler.END

    def setup_application(self):
        """Настраивает Telegram приложение"""
        from telegram.error import Conflict, NetworkError, TimedOut

        # Создаем приложение с обработкой ошибок
        builder = Application.builder()
        builder.token(self.token)
        builder.connection_pool_size(8)
        builder.pool_timeout(20.0)
        builder.read_timeout(30.0)
        builder.write_timeout(30.0)

        # Улучшенная обработка ошибок
        async def error_handler(update, context):
            error = context.error

            if isinstance(error, Conflict):
                bot_logger.warning("Конфликт Telegram API - возможно запущен другой экземпляр бота")
                await asyncio.sleep(5)
                return
            elif isinstance(error, (NetworkError, TimedOut)):
                bot_logger.warning(f"Сетевая ошибка Telegram: {error}")
                await asyncio.sleep(2)
                return
            else:
                bot_logger.error(f"Ошибка Telegram бота: {error}", exc_info=True)

        self.app = builder.build()
        self.app.add_error_handler(error_handler)

        # Создаем ConversationHandler
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.TEXT & ~filters.COMMAND, self.button_handler)
            ],
            states={
                self.ADDING_COIN: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_coin_handler)
                ],
                self.REMOVING_COIN: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.remove_coin_handler)
                ],
                self.SETTING_VOLUME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.volume_setting_handler)
                ],
                self.SETTING_SPREAD: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.spread_setting_handler)
                ],
                self.SETTING_NATR: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.natr_setting_handler)
                ]
            },
            fallbacks=[
                CommandHandler("start", self.start_handler),
                MessageHandler(filters.Regex("^🔙 Назад$"), self._handle_back)
            ],
            per_message=False
        )

        # Добавляем handlers
        self.app.add_handler(CommandHandler("start", self.start_handler))
        self.app.add_handler(conv_handler)

        return self.app

# Глобальный экземпляр бота
telegram_bot = TradingTelegramBot()
