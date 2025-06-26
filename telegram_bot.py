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
from optimized_api_client import optimized_api_client
from watchlist_manager import watchlist_manager
from bot_state import bot_state_manager
from advanced_alerts import advanced_alert_manager, AlertType, AlertSeverity
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
            ["📈 NATR", "🚨 Алерты"],
            ["🔄 Сброс", "🔙 Назад"]
        ], resize_keyboard=True)

        self.back_keyboard = ReplyKeyboardMarkup([
            ["🔙 Назад"]
        ], resize_keyboard=True)

    async def _rate_limit_message(self):
        """Ограничение частоты отправки сообщений"""
        current_time = time.time()
        min_interval = 0.5  # Уменьшили интервал для быстрых уведомлений

        if current_time - self.last_message_time < min_interval:
            await asyncio.sleep(min_interval - (current_time - self.last_message_time))

        self.last_message_time = time.time()

    async def send_message(self, text: str, reply_markup=None, parse_mode=ParseMode.HTML) -> Optional[int]:
        """Отправляет сообщение с минимальными задержками"""
        if not self.app or not self.app.bot:
            return None

        try:
            current_loop = asyncio.get_running_loop()
            if current_loop.is_closed():
                return None
        except RuntimeError:
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
            error_message = str(e).lower()
            if any(phrase in error_message for phrase in [
                "event loop", "different event loop", "asyncio.locks.event",
                "is bound to a different event loop", "runtimeerror"
            ]):
                bot_logger.debug(f"Event loop ошибка при отправке: {type(e).__name__}")
                return None
            else:
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
            bot_logger.debug(f"Ошибка редактирования сообщения: {type(e).__name__}")

    async def delete_message(self, message_id: int) -> bool:
        """Удаляет сообщение быстро"""
        if not message_id or not isinstance(message_id, int) or message_id <= 0:
            return False

        if not self.app or not self.app.bot:
            return False

        try:
            await asyncio.wait_for(
                self.app.bot.delete_message(chat_id=self.chat_id, message_id=message_id),
                timeout=2.0
            )
            return True

        except asyncio.TimeoutError:
            return False
        except Exception as e:
            error_message = str(e).lower()
            ignored_errors = [
                "message to delete not found", "message can't be deleted", "message is too old",
                "bad request", "not found", "event loop", "different event loop", 
                "asyncio.locks.event", "runtimeerror", "is bound to a different event loop",
                "cannot be called from a running event loop", "event loop is closed",
                "networkerror", "unknown error in http implementation"
            ]

            if any(phrase in error_message for phrase in ignored_errors):
                return False
            else:
                bot_logger.warning(f"Необработанная ошибка удаления сообщения {message_id}: {e}")
                return False

    def _chunks(self, lst: List, size: int):
        """Разбивает список на чанки"""
        for i in range(0, len(lst), size):
            yield lst[i:i + size]

    async def _stop_current_mode(self):
        """Быстрая остановка текущего режима"""
        if self.bot_mode:
            bot_logger.info(f"🛑 Остановка режима: {self.bot_mode}")
            self.bot_running = False

            # Быстрая очистка без ожидания
            try:
                if self.monitoring_message_id:
                    asyncio.create_task(self.delete_message(self.monitoring_message_id))
                    self.monitoring_message_id = None

                if self.active_coins:
                    for coin_data in self.active_coins.values():
                        if coin_data.get('msg_id'):
                            asyncio.create_task(self.delete_message(coin_data['msg_id']))
                    self.active_coins.clear()

            except Exception as e:
                bot_logger.debug(f"Ошибка очистки: {type(e).__name__}")
                self.monitoring_message_id = None
                self.active_coins.clear()

            self.bot_mode = None
            bot_state_manager.set_last_mode(None)

    async def _notification_mode_loop_ultra(self):
        """Ультра-быстрый цикл уведомлений"""
        bot_logger.info("🚀 Запущен ультра-быстрый режим уведомлений")

        while self.bot_running and self.bot_mode == 'notification':
            watchlist = watchlist_manager.get_all()
            if not watchlist:
                await asyncio.sleep(0.5)
                continue

            try:
                # Получаем данные всех монет одним батчем
                coin_data_batch = await optimized_api_client.get_batch_coin_data_ultra(watchlist)

                # Обрабатываем результаты параллельно
                tasks = []
                for symbol, data in coin_data_batch.items():
                    if data:
                        task = self._process_coin_notification_fast(symbol, data)
                        tasks.append(task)

                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

            except Exception as e:
                bot_logger.error(f"Ошибка в цикле уведомлений: {e}")

            # Минимальная задержка для максимальной скорости
            await asyncio.sleep(0.3)  # Очень быстрое обновление

    async def _process_coin_notification_fast(self, symbol: str, data: Dict):
        """Быстрая обработка уведомления для монеты"""
        now = time.time()
        is_currently_active = symbol in self.active_coins

        # Проверяем алерты асинхронно
        try:
            advanced_alert_manager.check_coin_alerts(symbol, data)
        except Exception:
            pass

        if data['active']:
            if not is_currently_active:
                # Новая активная монета - отправляем уведомление
                message = (
                    f"🚨 <b>{symbol}_USDT активен</b>\n"
                    f"💰 ${data['price']:.6f} | 🔄 {data['change']:+.2f}%\n"
                    f"📊 ${data['volume']:,.0f} | ⇄ {data['spread']:.2f}%\n"
                    f"📈 NATR: {data['natr']:.2f}% | 🔁 {data['trades']}"
                )

                # Отправляем асинхронно без ожидания
                msg_task = asyncio.create_task(self.send_message(message))

                self.active_coins[symbol] = {
                    'start': now,
                    'last_active': now,
                    'msg_id': None,  # Заполним позже
                    'data': data,
                    'msg_task': msg_task
                }

                bot_logger.trade_activity(symbol, "STARTED", f"Volume: ${data['volume']:,.0f}")

                # Получаем ID сообщения асинхронно
                try:
                    msg_id = await msg_task
                    if msg_id:
                        self.active_coins[symbol]['msg_id'] = msg_id
                except Exception:
                    pass

            else:
                # Обновляем данные активной монеты
                self.active_coins[symbol]['last_active'] = now
                self.active_coins[symbol]['data'] = data

                # Обновляем сообщение если есть ID
                msg_id = self.active_coins[symbol].get('msg_id')
                if msg_id:
                    message = (
                        f"🚨 <b>{symbol}_USDT активен</b>\n"
                        f"💰 ${data['price']:.6f} | 🔄 {data['change']:+.2f}%\n"
                        f"📊 ${data['volume']:,.0f} | ⇄ {data['spread']:.2f}%\n"
                        f"📈 NATR: {data['natr']:.2f}% | 🔁 {data['trades']}"
                    )
                    asyncio.create_task(self.edit_message(msg_id, message))

        elif is_currently_active:
            # Проверяем время неактивности
            inactivity_timeout = config_manager.get('INACTIVITY_TIMEOUT', 180)
            if now - self.active_coins[symbol]['last_active'] > inactivity_timeout:
                asyncio.create_task(self._end_coin_activity_fast(symbol, now))

    async def _end_coin_activity_fast(self, symbol: str, end_time: float):
        """Быстрое завершение активности монеты"""
        try:
            coin_info = self.active_coins[symbol]
            duration = end_time - coin_info['start']

            # Удаляем сообщение асинхронно
            msg_id = coin_info.get('msg_id')
            if msg_id:
                asyncio.create_task(self.delete_message(msg_id))

            # Отправляем уведомление о завершении если активность была >= 60 секунд
            if duration >= 60:
                duration_min = int(duration // 60)
                duration_sec = int(duration % 60)
                end_message = (
                    f"✅ <b>{symbol}_USDT завершил</b>\n"
                    f"⏱ {duration_min}м {duration_sec}с"
                )
                asyncio.create_task(self.send_message(end_message))
                bot_logger.trade_activity(symbol, "ENDED", f"Duration: {duration_min}m {duration_sec}s")

            # Удаляем из активных монет
            del self.active_coins[symbol]

        except Exception as e:
            bot_logger.debug(f"Ошибка завершения активности {symbol}: {e}")

    async def _monitoring_mode_loop_ultra(self):
        """Ультра-быстрый цикл мониторинга"""
        bot_logger.info("🚀 Запущен ультра-быстрый режим мониторинга")

        # Отправляем начальное сообщение
        initial_text = "🚀 <b>Ультра-быстрый мониторинг активирован</b>"
        self.monitoring_message_id = await self.send_message(initial_text)

        cycle_count = 0
        while self.bot_running and self.bot_mode == 'monitoring':
            cycle_count += 1

            watchlist = watchlist_manager.get_all()
            if not watchlist:
                await asyncio.sleep(2)
                continue

            try:
                # Получаем данные всех монет одним батчем
                coin_data_batch = await optimized_api_client.get_batch_coin_data_ultra(watchlist)

                if coin_data_batch:
                    results = list(coin_data_batch.values())
                    failed_coins = [symbol for symbol in watchlist if symbol not in coin_data_batch]

                    # Обновляем отчет
                    report = self._format_monitoring_report_fast(results, failed_coins)
                    if self.monitoring_message_id:
                        asyncio.create_task(self.edit_message(self.monitoring_message_id, report))

            except Exception as e:
                bot_logger.error(f"Ошибка мониторинга: {e}")

            # Очистка памяти каждые 100 циклов
            if cycle_count % 100 == 0:
                import gc
                gc.collect()

            # Быстрое обновление
            await asyncio.sleep(0.5)  # Очень быстрое обновление

    def _format_monitoring_report_fast(self, results: List[Dict], failed_coins: List[str]) -> str:
        """Быстрое форматирование отчета мониторинга"""
        # Сортируем по объему
        results.sort(key=lambda x: x['volume'], reverse=True)

        parts = ["<b>🚀 Ультра-быстрый мониторинг</b>\n"]

        # Показываем активные монеты
        active_coins = [r for r in results if r['active']]
        if active_coins:
            parts.append(f"<b>🟢 АКТИВНЫЕ ({len(active_coins)}):</b>")
            for coin in active_coins[:8]:  # Ограничиваем для скорости
                parts.append(
                    f"• <b>{coin['symbol']}</b> "
                    f"${coin['volume']:,.0f} | {coin['change']:+.1f}% | "
                    f"S:{coin['spread']:.2f}% | N:{coin['natr']:.2f}%"
                )
            parts.append("")

        # Показываем неактивные монеты (топ)
        inactive_coins = [r for r in results if not r['active']]
        if inactive_coins:
            parts.append(f"<b>🔴 НЕАКТИВНЫЕ (топ {min(6, len(inactive_coins))}):</b>")
            for coin in inactive_coins[:6]:
                parts.append(
                    f"• <b>{coin['symbol']}</b> "
                    f"${coin['volume']:,.0f} | {coin['change']:+.1f}%"
                )

        # Статистика
        parts.append(f"\n📊 {len(active_coins)}/{len(results)} активных")
        if failed_coins:
            parts.append(f"⚠ {len(failed_coins)} ошибок")

        report = "\n".join(parts)

        # Обрезаем если слишком длинное
        if len(report) > 3500:
            report = report[:3500] + "\n..."

        return report

    def start_monitoring_loop(self):
        """Запускает ультра-быстрый цикл в отдельном потоке"""
        def run_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            if self.bot_mode == 'notification':
                loop.run_until_complete(self._notification_mode_loop_ultra())
            elif self.bot_mode == 'monitoring':
                loop.run_until_complete(self._monitoring_mode_loop_ultra())

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()
        return thread

    # Telegram Handlers (сокращенные для экономии места)
    async def start_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        last_mode = bot_state_manager.get_last_mode()

        welcome_text = (
            "🚀 <b>Ультра-быстрый торговый бот v2.1</b>\n\n"
            "⚡ <b>Режимы:</b>\n"
            "• 🔔 <b>Уведомления</b> - мгновенные алерты\n"
            "• 📊 <b>Мониторинг</b> - реальное время\n\n"
        )

        # Автовосстановление
        if last_mode and not self.bot_running:
            if last_mode == 'notification':
                welcome_text += "🔄 <b>Восстанавливаю уведомления...</b>\n\n"
                await update.message.reply_text(welcome_text + "Выберите действие:", reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML)

                self.bot_mode = 'notification'
                self.bot_running = True
                self.start_monitoring_loop()

                await self.send_message("✅ <b>Ультра-быстрые уведомления активны</b>")
                return

            elif last_mode == 'monitoring':
                welcome_text += "🔄 <b>Восстанавливаю мониторинг...</b>\n\n"
                await update.message.reply_text(welcome_text + "Выберите действие:", reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML)

                self.bot_mode = 'monitoring'
                self.bot_running = True
                self.start_monitoring_loop()

                await self.send_message("✅ <b>Ультра-быстрый мониторинг активен</b>")
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
            elif text == "🚨 Алерты":
                await self._handle_alerts(update)
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
        """Обработка ультра-быстрого режима уведомлений"""
        if self.bot_running and self.bot_mode == 'notification':
            await update.message.reply_text(
                "✅ Ультра-быстрые уведомления уже активны.",
                reply_markup=self.main_keyboard
            )
            return

        await self._stop_current_mode()
        await asyncio.sleep(0.5)

        self.bot_mode = 'notification'
        self.bot_running = True
        bot_state_manager.set_last_mode('notification')

        try:
            await update.message.reply_text(
                "🚀 <b>Ультра-быстрые уведомления активированы</b>\n"
                "⚡ Мгновенные алерты об активных монетах\n"
                "🎯 Обновление каждые 0.3 секунды",
                reply_markup=self.main_keyboard,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            bot_logger.debug(f"Ошибка отправки подтверждения: {type(e).__name__}")

        self.start_monitoring_loop()

    async def _handle_monitoring_mode(self, update: Update):
        """Обработка ультра-быстрого режима мониторинга"""
        if self.bot_running and self.bot_mode == 'monitoring':
            await update.message.reply_text(
                "✅ Ультра-быстрый мониторинг уже активен.",
                reply_markup=self.main_keyboard
            )
            return

        await self._stop_current_mode()
        await asyncio.sleep(0.5)

        self.bot_mode = 'monitoring'
        self.bot_running = True
        bot_state_manager.set_last_mode('monitoring')

        try:
            await update.message.reply_text(
                "🚀 <b>Ультра-быстрый мониторинг активирован</b>\n"
                "📊 Реальное время обновления\n"
                "⚡ Обновление каждые 0.5 секунд",
                reply_markup=self.main_keyboard,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            bot_logger.debug(f"Ошибка отправки подтверждения: {type(e).__name__}")

        self.start_monitoring_loop()

    async def _handle_stop(self, update: Update):
        """Обработка остановки бота"""
        await self._stop_current_mode()

        try:
            await update.message.reply_text(
                "🛑 <b>Бот остановлен</b>",
                reply_markup=self.main_keyboard,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            bot_logger.debug(f"Ошибка отправки подтверждения остановки: {type(e).__name__}")

    # Остальные методы остаются теми же, но сокращены для экономии места
    async def _handle_add_coin_start(self, update: Update):
        await self._stop_current_mode()
        await update.message.reply_text(
            "➕ <b>Добавление монеты</b>\n\nВведите символ (например: <code>BTC</code>):",
            reply_markup=self.back_keyboard, parse_mode=ParseMode.HTML
        )
        return self.ADDING_COIN

    async def _handle_remove_coin_start(self, update: Update):
        await self._stop_current_mode()
        if watchlist_manager.size() == 0:
            await update.message.reply_text("❌ Список пуст.", reply_markup=self.main_keyboard)
            return ConversationHandler.END

        await update.message.reply_text(
            "➖ <b>Удаление монеты</b>\n\nВведите символ для удаления:",
            reply_markup=self.back_keyboard, parse_mode=ParseMode.HTML
        )
        return self.REMOVING_COIN

    async def _handle_show_list(self, update: Update):
        await self._stop_current_mode()
        coins = watchlist_manager.get_all()
        if not coins:
            text = "📋 <b>Список пуст</b>"
        else:
            text = f"📋 <b>Список ({len(coins)} монет):</b>\n\n"
            sorted_coins = sorted(coins)
            for i in range(0, len(sorted_coins), 5):
                batch = sorted_coins[i:i+5]
                text += " • ".join(batch) + "\n"

        await update.message.reply_text(text, reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML)

    async def _handle_settings(self, update: Update):
        await self._stop_current_mode()
        text = (
            "⚙ <b>Настройки фильтров:</b>\n\n"
            f"📊 Объём: <code>${config_manager.get('VOLUME_THRESHOLD'):,}</code>\n"
            f"⇄ Спред: <code>{config_manager.get('SPREAD_THRESHOLD')}%</code>\n"
            f"📈 NATR: <code>{config_manager.get('NATR_THRESHOLD')}%</code>\n\n"
            "Выберите параметр:"
        )
        await update.message.reply_text(text, reply_markup=self.settings_keyboard, parse_mode=ParseMode.HTML)

    async def _handle_status(self, update: Update):
        status_parts = ["ℹ <b>Статус ультра-быстрого бота:</b>\n"]
        if self.bot_running:
            status_parts.append(f"🚀 Работает: <b>{self.bot_mode}</b>")
            if self.bot_mode == 'notification':
                status_parts.append(f"⚡ Активных: <b>{len(self.active_coins)}</b>")
        else:
            status_parts.append("🔴 Остановлен")

        status_parts.append(f"📋 Монет: <b>{watchlist_manager.size()}</b>")
        status_parts.append(f"📊 Объём: ${config_manager.get('VOLUME_THRESHOLD'):,}")

        await update.message.reply_text(
            "\n".join(status_parts), reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML
        )

    async def _handle_back(self, update: Update):
        await update.message.reply_text("🏠 Главное меню:", reply_markup=self.main_keyboard)

    # Сокращенные handlers для настроек
    async def _handle_volume_setting_start(self, update: Update):
        await self._stop_current_mode()
        current_value = config_manager.get('VOLUME_THRESHOLD')
        await update.message.reply_text(
            f"📊 <b>Настройка объёма</b>\n\nТекущее: <code>${current_value:,}</code>\n\nВведите новое значение:",
            reply_markup=self.back_keyboard, parse_mode=ParseMode.HTML
        )
        return self.SETTING_VOLUME

    async def _handle_spread_setting_start(self, update: Update):
        await self._stop_current_mode()
        current_value = config_manager.get('SPREAD_THRESHOLD')
        await update.message.reply_text(
            f"⇄ <b>Настройка спреда</b>\n\nТекущее: <code>{current_value}%</code>\n\nВведите новое значение:",
            reply_markup=self.back_keyboard, parse_mode=ParseMode.HTML
        )
        return self.SETTING_SPREAD

    async def _handle_natr_setting_start(self, update: Update):
        await self._stop_current_mode()
        current_value = config_manager.get('NATR_THRESHOLD')
        await update.message.reply_text(
            f"📈 <b>Настройка NATR</b>\n\nТекущее: <code>{current_value}%</code>\n\nВведите новое значение:",
            reply_markup=self.back_keyboard, parse_mode=ParseMode.HTML
        )
        return self.SETTING_NATR

    async def _handle_alerts(self, update: Update):
        await self._stop_current_mode()
        await update.message.reply_text(
            "🚨 <b>Система алертов активна</b>\n\n"
            "⚡ Работает в фоновом режиме\n"
            "🎯 Мгновенные уведомления о важных событиях",
            reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML
        )

    async def _handle_reset_settings(self, update: Update):
        await self._stop_current_mode()
        config_manager.set('VOLUME_THRESHOLD', 1000)
        config_manager.set('SPREAD_THRESHOLD', 0.1)
        config_manager.set('NATR_THRESHOLD', 0.5)

        await update.message.reply_text(
            "🔄 <b>Настройки сброшены</b>\n\n"
            "📊 Объём: $1,000\n⇄ Спред: 0.1%\n📈 NATR: 0.5%",
            reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML
        )

    # Handlers для ConversationHandler
    async def add_coin_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if text == "🔙 Назад":
            await self._handle_back(update)
            return ConversationHandler.END

        symbol = text.upper().replace("_USDT", "").replace("USDT", "")
        if not symbol or len(symbol) < 2:
            await update.message.reply_text("❌ Некорректный символ.", reply_markup=self.back_keyboard)
            return self.ADDING_COIN

        if watchlist_manager.contains(symbol):
            await update.message.reply_text(
                f"⚠ <b>{symbol}</b> уже в списке.",
                reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END

        await update.message.reply_text("🔄 Проверяю...")
        try:
            coin_data = await optimized_api_client.get_optimized_coin_data(symbol)
            if coin_data:
                watchlist_manager.add(symbol)
                await update.message.reply_text(
                    f"✅ <b>{symbol}_USDT</b> добавлена\n"
                    f"💰 ${coin_data['price']:.6f} | 📊 ${coin_data['volume']:,.0f}",
                    reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    f"❌ <b>{symbol}_USDT</b> недоступна.",
                    reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML
                )
        except Exception as e:
            await update.message.reply_text(
                f"❌ Ошибка проверки <b>{symbol}</b>.",
                reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML
            )

        return ConversationHandler.END

    async def remove_coin_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if text == "🔙 Назад":
            await self._handle_back(update)
            return ConversationHandler.END

        symbol = text.upper().replace("_USDT", "").replace("USDT", "")
        if watchlist_manager.remove(symbol):
            await update.message.reply_text(
                f"✅ <b>{symbol}</b> удалена.",
                reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                f"❌ <b>{symbol}</b> не найдена.",
                reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML
            )
        return ConversationHandler.END

    async def volume_setting_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):text = update.message.text.strip()
        if text == "🔙 Назад":
            await self._handle_settings(update)
            return ConversationHandler.END

        try:
            value = int(text)
            if value < 100:
                await update.message.reply_text("❌ Минимум $100.", reply_markup=self.back_keyboard)
                return self.SETTING_VOLUME

            config_manager.set('VOLUME_THRESHOLD', value)
            await update.message.reply_text(
                f"✅ <b>Объём установлен:</b> ${value:,}",
                reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML
            )
        except ValueError:
            await update.message.reply_text("❌ Введите число.", reply_markup=self.back_keyboard)
            return self.SETTING_VOLUME
        return ConversationHandler.END

    async def spread_setting_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if text == "🔙 Назад":
            await self._handle_settings(update)
            return ConversationHandler.END

        try:
            value = float(text)
            if value < 0 or value > 10:
                await update.message.reply_text("❌ От 0 до 10%.", reply_markup=self.back_keyboard)
                return self.SETTING_SPREAD

            config_manager.set('SPREAD_THRESHOLD', value)
            await update.message.reply_text(
                f"✅ <b>Спред установлен:</b> {value}%",
                reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML
            )
        except ValueError:
            await update.message.reply_text("❌ Введите число.", reply_markup=self.back_keyboard)
            return self.SETTING_SPREAD
        return ConversationHandler.END

    async def natr_setting_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if text == "🔙 Назад":
            await self._handle_settings(update)
            return ConversationHandler.END

        try:
            value = float(text)
            if value < 0 or value > 20:
                await update.message.reply_text("❌ От 0 до 20%.", reply_markup=self.back_keyboard)
                return self.SETTING_NATR

            config_manager.set('NATR_THRESHOLD', value)
            await update.message.reply_text(
                f"✅ <b>NATR установлен:</b> {value}%",
                reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML
            )
        except ValueError:
            await update.message.reply_text("❌ Введите число.", reply_markup=self.back_keyboard)
            return self.SETTING_NATR
        return ConversationHandler.END

    def setup_application(self):
        """Настраивает Telegram приложение"""
        from telegram.error import Conflict, NetworkError, TimedOut

        builder = Application.builder()
        builder.token(self.token)
        builder.connection_pool_size(16)  # Увеличили для скорости
        builder.pool_timeout(10.0)  # Уменьшили timeout
        builder.read_timeout(15.0)
        builder.write_timeout(15.0)

        async def error_handler(update, context):
            error = context.error
            error_str = str(error).lower()

            if any(phrase in error_str for phrase in [
                "event loop", "different event loop", "asyncio.locks.event",
                "is bound to a different event loop", "unknown error in http implementation"
            ]):
                return

            if isinstance(error, Conflict):
                bot_logger.warning("Конфликт Telegram API")
                await asyncio.sleep(2)
                return
            elif isinstance(error, (NetworkError, TimedOut)):
                bot_logger.warning(f"Сетевая ошибка: {error}")
                await asyncio.sleep(1)
                return
            else:
                bot_logger.error(f"Ошибка бота: {error}", exc_info=True)

        self.app = builder.build()
        self.app.add_error_handler(error_handler)

        conv_handler = ConversationHandler(
            entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, self.button_handler)],
            states={
                self.ADDING_COIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_coin_handler)],
                self.REMOVING_COIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.remove_coin_handler)],
                self.SETTING_VOLUME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.volume_setting_handler)],
                self.SETTING_SPREAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.spread_setting_handler)],
                self.SETTING_NATR: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.natr_setting_handler)]
            },
            fallbacks=[
                CommandHandler("start", self.start_handler),
                MessageHandler(filters.Regex("^🔙 Назад$"), self._handle_back)
            ],
            per_message=False
        )

        self.app.add_handler(CommandHandler("start", self.start_handler))
        self.app.add_handler(conv_handler)

        return self.app

# Глобальный экземпляр бота
telegram_bot = TradingTelegramBot()