import asyncio
import time
from typing import Dict, Optional, List, Any
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, ConversationHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from logger import bot_logger
from config import config_manager
from api_client import api_client
from watchlist_manager import watchlist_manager
from bot_state import bot_state_manager
from advanced_alerts import advanced_alert_manager, AlertType, AlertSeverity
from notification_mode import NotificationMode
from monitoring_mode import MonitoringMode
from input_validator import input_validator
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
        self.last_message_time = 0
        self.message_cache = {}
        self._message_queue = asyncio.Queue()
        self._queue_processor_task = None

        # Защита от одновременных операций
        self._operation_lock = asyncio.Lock()
        self._switching_mode = False
        self._last_operation_time = 0

        # Модули режимов
        self.notification_mode = NotificationMode(self)
        self.monitoring_mode = MonitoringMode(self)

        # Состояния ConversationHandler
        self.ADDING_COIN, self.REMOVING_COIN = range(2)
        self.SETTING_VOLUME, self.SETTING_SPREAD, self.SETTING_NATR = range(2, 5)

        self._setup_keyboards()

    @property
    def active_coins(self):
        """Свойство для обратной совместимости с основным health check"""
        if self.bot_mode == 'notification':
            return self.notification_mode.active_coins
        return {}

    def _setup_keyboards(self):
        """Настраивает клавиатуры"""
        self.main_keyboard = ReplyKeyboardMarkup([
            ["🔔 Уведомления", "📊 Мониторинг"],
            ["➕ Добавить", "➖ Удалить"],
            ["📋 Список", "⚙ Настройки"],
            ["📈 Активность 24ч", "ℹ Статус"],
            ["🛑 Стоп"]
        ], resize_keyboard=True, one_time_keyboard=False)

        self.settings_keyboard = ReplyKeyboardMarkup([
            ["📊 Объём", "⇄ Спред"],
            ["📈 NATR", "🔄 Сброс"],
            ["🔙 Назад"]
        ], resize_keyboard=True)

        self.back_keyboard = ReplyKeyboardMarkup([
            ["🔙 Назад"]
        ], resize_keyboard=True)

    async def _start_message_queue_processor(self):
        """Запускает процессор очереди сообщений"""
        try:
            # Принудительно пересоздаем очередь в текущем event loop
            try:
                current_loop = asyncio.get_running_loop()
                if self._message_queue is not None:
                    # Проверяем привязку к event loop
                    try:
                        self._message_queue.qsize()  # Тест доступности
                    except RuntimeError:
                        # Очередь привязана к другому loop
                        self._message_queue = None

                if self._message_queue is None:
                    self._message_queue = asyncio.Queue()
                    bot_logger.debug("🔄 Создана новая очередь сообщений")

            except Exception as e:
                bot_logger.debug(f"Пересоздание очереди: {e}")
                self._message_queue = asyncio.Queue()

            # Останавливаем старую задачу если есть
            if self._queue_processor_task and not self._queue_processor_task.done():
                self._queue_processor_task.cancel()
                try:
                    await self._queue_processor_task
                except asyncio.CancelledError:
                    pass

            # Запускаем новую задачу
            self._queue_processor_task = asyncio.create_task(self._process_message_queue())
            bot_logger.debug("🔄 Процессор очереди сообщений запущен")

        except Exception as e:
            bot_logger.error(f"Ошибка запуска процессора очереди: {e}")

    async def _process_message_queue(self):
        """Обрабатывает очередь сообщений последовательно"""
        consecutive_errors = 0
        max_consecutive_errors = 5

        while self.bot_running and consecutive_errors < max_consecutive_errors:
            try:
                if self._message_queue is None:
                    await asyncio.sleep(1.0)
                    continue

                # Ждем сообщение из очереди с таймаутом
                try:
                    message_data = await asyncio.wait_for(
                        self._message_queue.get(), 
                        timeout=2.0
                    )
                    consecutive_errors = 0  # Сбрасываем счетчик при успехе

                    await self._execute_telegram_message(message_data)
                    await asyncio.sleep(0.1)  # Минимальная задержка между сообщениями

                except asyncio.TimeoutError:
                    consecutive_errors = 0  # Таймаут не считается ошибкой
                    continue
                except RuntimeError as e:
                    if "different event loop" in str(e):
                        bot_logger.warning("🔄 Переинициализация очереди из-за смены event loop")
                        self._message_queue = asyncio.Queue()
                        consecutive_errors = 0
                        continue
                    else:
                        raise

            except Exception as e:
                consecutive_errors += 1
                bot_logger.error(f"Ошибка в процессоре очереди сообщений ({consecutive_errors}/{max_consecutive_errors}): {e}")
                await asyncio.sleep(min(0.5 * consecutive_errors, 3.0))  # Экспоненциальная задержка

        if consecutive_errors >= max_consecutive_errors:
            bot_logger.error("🚨 Процессор очереди остановлен из-за множественных ошибок")
            self._queue_processor_task = None

    async def _execute_telegram_message(self, message_data: Dict):
        """Выполняет отправку Telegram сообщения"""
        try:
            action = message_data['action']

            if action == 'send':
                response = await self._direct_telegram_send(
                    message_data['text'],
                    message_data.get('reply_markup'),
                    message_data.get('parse_mode', ParseMode.HTML)
                )

                # Возвращаем результат через callback если есть
                if 'callback' in message_data:
                    message_data['callback'](response)

            elif action == 'edit':
                await self._direct_telegram_edit(
                    message_data['message_id'],
                    message_data['text'],
                    message_data.get('reply_markup')
                )

            elif action == 'delete':
                await self._direct_telegram_delete(message_data['message_id'])

        except Exception as e:
            bot_logger.error(f"Ошибка выполнения Telegram операции: {e}")

    async def _direct_telegram_send(self, text: str, reply_markup=None, parse_mode=ParseMode.HTML):
        """Прямая отправка через Telegram API"""
        if not self.app or not self.app.bot:
            return None

        try:
            current_time = time.time()
            if current_time - self.last_message_time < 0.5:
                await asyncio.sleep(0.5 - (current_time - self.last_message_time))

            message = await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )

            self.last_message_time = time.time()

            if message and hasattr(message, 'message_id'):
                return message.message_id
            return None

        except Exception as e:
            bot_logger.debug(f"Прямая отправка не удалась: {type(e).__name__}")
            return None

    async def _direct_telegram_edit(self, message_id: int, text: str, reply_markup=None):
        """Прямое редактирование через Telegram API"""
        if not self.app or not self.app.bot:
            return

        try:
            await self.app.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            if "message is not modified" not in str(e).lower():
                bot_logger.debug(f"Редактирование сообщения {message_id} не удалось: {type(e).__name__}")

    async def _direct_telegram_delete(self, message_id: int):
        """Прямое удаление через Telegram API"""
        if not self.app or not self.app.bot:
            return

        try:
            await self.app.bot.delete_message(chat_id=self.chat_id, message_id=message_id)
            if message_id in self.message_cache:
                del self.message_cache[message_id]
        except Exception as e:
            bot_logger.debug(f"Удаление сообщения {message_id} не удалось: {type(e).__name__}")

    async def send_message(self, text: str, reply_markup=None, parse_mode=ParseMode.HTML) -> Optional[int]:
        """Отправляет сообщение через очередь с callback для получения результата"""
        if not self.bot_running:
            return None

        # Проверяем доступность очереди
        if self._message_queue is None:
            bot_logger.warning("Очередь недоступна, прямая отправка")
            return await self._direct_telegram_send(text, reply_markup, parse_mode)

        # Создаем Future для получения результата
        result_future = asyncio.Future()

        def callback(result):
            if not result_future.done():
                result_future.set_result(result)

        # Добавляем в очередь
        message_data = {
            'action': 'send',
            'text': text,
            'reply_markup': reply_markup,
            'parse_mode': parse_mode,
            'callback': callback
        }

        try:
            # Проверяем возможность добавления в очередь
            try:
                await self._message_queue.put(message_data)
            except RuntimeError as e:
                if "different event loop" in str(e):
                    bot_logger.warning("Event loop конфликт, прямая отправка")
                    return await self._direct_telegram_send(text, reply_markup, parse_mode)
                else:
                    raise

            # Ждем результат с таймаутом
            result = await asyncio.wait_for(result_future, timeout=10.0)

            if result:
                bot_logger.info(f"[SEND_MESSAGE_SUCCESS] Сообщение отправлено успешно, msg_id: {result}")
            return result

        except asyncio.TimeoutError:
            bot_logger.error("[SEND_MESSAGE_TIMEOUT] Таймаут отправки сообщения")
            return None
        except Exception as e:
            bot_logger.error(f"[SEND_MESSAGE_ERROR] Ошибка отправки: {e}")
            # Fallback на прямую отправку
            return await self._direct_telegram_send(text, reply_markup, parse_mode)

    async def edit_message(self, message_id: int, text: str, reply_markup=None):
        """Редактирует сообщение через очередь"""
        if not self.bot_running:
            return

        # Проверяем изменения в кеше
        cached_message = self.message_cache.get(message_id)
        if cached_message == text:
            return

        message_data = {
            'action': 'edit',
            'message_id': message_id,
            'text': text,
            'reply_markup': reply_markup
        }

        try:
            await self._message_queue.put(message_data)
            self.message_cache[message_id] = text
        except Exception as e:
            bot_logger.debug(f"Ошибка добавления edit в очередь: {e}")

    async def delete_message(self, message_id: int) -> bool:
        """Удаляет сообщение через очередь"""
        if not message_id or not isinstance(message_id, int) or message_id <= 0:
            return False

        if not self.bot_running:
            return False

        message_data = {
            'action': 'delete',
            'message_id': message_id
        }

        try:
            await self._message_queue.put(message_data)
            return True
        except Exception as e:
            bot_logger.debug(f"Ошибка добавления delete в очередь: {e}")

    def _chunks(self, lst: List, size: int):
        """Разбивает список на чанки"""
        for i in range(0, len(lst), size):
            yield lst[i:i + size]

    async def _stop_current_mode(self):
        """Останавливает текущий режим работы бота с защитой от одновременных операций"""
        async with self._operation_lock:
            if self._switching_mode:
                bot_logger.debug("Переключение режима уже в процессе, пропускаем")
                return

            if not self.bot_mode:
                return

            self._switching_mode = True

            try:
                bot_logger.info(f"🛑 Останавливаем режим: {self.bot_mode}")

                # Останавливаем соответствующий модуль
                try:
                    if self.bot_mode == 'notification':
                        await asyncio.wait_for(self.notification_mode.stop(), timeout=5.0)
                    elif self.bot_mode == 'monitoring':
                        await asyncio.wait_for(self.monitoring_mode.stop(), timeout=5.0)
                except asyncio.TimeoutError:
                    bot_logger.warning("Таймаут остановки режима, принудительно завершаем")
                except Exception as e:
                    bot_logger.error(f"Ошибка остановки режима: {e}")

                # Останавливаем процессор очереди
                if self._queue_processor_task and not self._queue_processor_task.done():
                    self._queue_processor_task.cancel()
                    try:
                        await asyncio.wait_for(self._queue_processor_task, timeout=1.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                    self._queue_processor_task = None

                # Очищаем очередь сообщений безопасно
                try:
                    if self._message_queue:
                        queue_size = 0
                        while not self._message_queue.empty() and queue_size < 100:
                            try:
                                self._message_queue.get_nowait()
                                queue_size += 1
                            except asyncio.QueueEmpty:
                                break

                        # Пересоздаем очередь
                        self._message_queue = asyncio.Queue()

                except Exception as e:
                    bot_logger.debug(f"Ошибка очистки очереди: {e}")
                    # Принудительно пересоздаем очередь
                    self._message_queue = asyncio.Queue()

                # Очищаем состояние
                self.bot_running = False
                self.bot_mode = None
                self.message_cache.clear()
                bot_state_manager.set_last_mode(None)

                # Даем время для завершения операций
                await asyncio.sleep(0.3)

                bot_logger.info("✅ Режим успешно остановлен")

            finally:
                self._switching_mode = False



    # Telegram Handlers
    async def start_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        last_mode = bot_state_manager.get_last_mode()

        welcome_text = (
            "🤖 <b>Добро пожаловать в MEXCScalping Assistant!</b>\n\n"
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
            welcome_text += f"🔄 <b>Восстанавливаю режим {last_mode}...</b>\n\n"
            await update.message.reply_text(welcome_text + "Выберите действие:", reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML)

            self.bot_mode = last_mode
            self.bot_running = True

            # Запускаем процессор очереди
            await self._start_message_queue_processor()

            if last_mode == 'notification':
                await self.notification_mode.start()
                await self.send_message(
                    "✅ <b>Режим уведомлений восстановлен</b>\n"
                    "Вы будете получать уведомления об активных монетах."
                )
            elif last_mode == 'monitoring':
                await self.monitoring_mode.start()
                await self.send_message(
                    "✅ <b>Режим мониторинга восстановлен</b>\n"
                    "Сводка будет обновляться автоматически."
                )
            return

        welcome_text += "Выберите действие:"
        await update.message.reply_text(welcome_text, reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML)

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Основной обработчик кнопок с защитой от spam"""
        text = update.message.text
        current_time = time.time()

        # Защита от spam нажатий (минимум 1 секунда между операциями)
        if current_time - self._last_operation_time < 1.0:
            bot_logger.debug("Слишком быстрые нажатия, игнорируем")
            return ConversationHandler.END

        self._last_operation_time = current_time

        try:
            # Проверяем, не идет ли уже переключение режима
            if self._switching_mode:
                await update.message.reply_text(
                    "⏳ Идет переключение режима, подождите...",
                    reply_markup=self.main_keyboard
                )
                return ConversationHandler.END

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
            elif text == "📈 Активность 24ч":
                await self._handle_activity_24h(update)
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
            try:
                await update.message.reply_text(
                    "❌ Произошла ошибка. Попробуйте еще раз через несколько секунд.",
                    reply_markup=self.main_keyboard
                )
            except Exception as reply_error:
                bot_logger.error(f"Не удалось отправить сообщение об ошибке: {reply_error}")

        return ConversationHandler.END

    async def _handle_notification_mode(self, update: Update):
        """Обработка режима уведомлений"""
        if self.bot_running and self.bot_mode == 'notification':
            await update.message.reply_text(
                "✅ Бот уже работает в режиме уведомлений.",
                reply_markup=self.main_keyboard
            )
            return

        await self._stop_current_mode()
        await asyncio.sleep(0.5)

        self.bot_mode = 'notification'
        self.bot_running = True
        bot_state_manager.set_last_mode('notification')

        # Запускаем процессор очереди сообщений
        await self._start_message_queue_processor()

        # Запускаем модуль уведомлений (он сам отправит сообщение)
        await self.notification_mode.start()

        bot_logger.info("🔔 Режим уведомлений успешно активирован")

    async def _handle_monitoring_mode(self, update: Update):
        """Обработка режима мониторинга"""
        if self.bot_running and self.bot_mode == 'monitoring':
            await update.message.reply_text(
                "✅ Бот уже работает в режиме мониторинга.",
                reply_markup=self.main_keyboard
            )
            return

        await self._stop_current_mode()
        await asyncio.sleep(0.5)

        self.bot_mode = 'monitoring'
        self.bot_running = True
        bot_state_manager.set_last_mode('monitoring')

        # Запускаем процессор очереди сообщений
        await self._start_message_queue_processor()

        # Запускаем модуль мониторинга (он сам отправит сообщение)
        await self.monitoring_mode.start()

        bot_logger.info("📊 Режим мониторинга успешно активирован")

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
        await update.message.reply_text(
            "➕ <b>Добавление монеты</b>\n\n"
            "Введите символ монеты (например: <code>BTC</code> или <code>BTC_USDT</code>):",
            reply_markup=self.back_keyboard,
            parse_mode=ParseMode.HTML
        )
        return self.ADDING_COIN

    async def _handle_remove_coin_start(self, update: Update):
        """Начало удаления монеты"""
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
        coins = watchlist_manager.get_all()
        if not coins:
            text = "📋 <b>Список отслеживания пуст</b>"
        else:
            sorted_coins = sorted(coins)
            text = f"📋 <b>Список отслеживания ({len(coins)} монет):</b>\n\n"

            for i in range(0, len(sorted_coins), 5):
                batch = sorted_coins[i:i+5]
                text += " • ".join(batch) + "\n"

        await update.message.reply_text(text, reply_markup=self.main_keyboard, parse_mode=ParseMode.HTML)

    async def _handle_settings(self, update: Update):
        """Обработка настроек"""
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
                notification_stats = self.notification_mode.get_stats()
                status_parts.append(f"📊 Активных монет: <b>{notification_stats['active_coins_count']}</b>")
                if notification_stats['active_coins']:
                    status_parts.append(f"• Монеты: {', '.join(notification_stats['active_coins'][:5])}")

            elif self.bot_mode == 'monitoring':
                monitoring_stats = self.monitoring_mode.get_stats()
                status_parts.append(f"📋 Отслеживается: <b>{monitoring_stats['watchlist_size']}</b> монет")
        else:
            status_parts.append("🔴 Остановлен")

        status_parts.append(f"📋 Монет в списке: <b>{watchlist_manager.size()}</b>")

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

    async def _handle_activity_24h(self, update: Update):
        """Показ активности монет за последние 24 часа"""
        try:
            from datetime import datetime, timedelta
            import json
            import os
            
            # Определяем даты для проверки (сегодня и вчера)
            today = datetime.now().strftime('%Y-%m-%d')
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            
            # Время 24 часа назад
            cutoff_time = time.time() - 24 * 3600
            
            all_sessions = []
            total_sessions = 0
            total_duration = 0
            total_volume = 0
            total_trades = 0
            unique_coins = set()
            
            # Читаем файлы за сегодня и вчера
            for date in [today, yesterday]:
                filename = f"sessions_{date}.json"
                filepath = os.path.join("session_data", filename)
                
                if os.path.exists(filepath):
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            daily_data = json.load(f)
                            
                        # Фильтруем сессии по времени (последние 24 часа)
                        for session in daily_data.get('sessions', []):
                            start_time = session.get('start_time', 0)
                            if start_time >= cutoff_time:
                                all_sessions.append(session)
                                total_sessions += 1
                                total_duration += session.get('total_duration', 0)
                                summary = session.get('summary', {})
                                total_volume += summary.get('total_volume', 0)
                                total_trades += summary.get('total_trades', 0)
                                unique_coins.add(session.get('symbol', ''))
                                
                    except Exception as e:
                        bot_logger.debug(f"Ошибка чтения {filename}: {e}")
            
            if not all_sessions:
                await update.message.reply_text(
                    "📈 <b>Активность за последние 24 часа</b>\n\n"
                    "❌ Нет данных об активности за последние 24 часа.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.main_keyboard
                )
                return
            
            # Сортируем сессии по времени начала (новые сначала)
            all_sessions.sort(key=lambda x: x.get('start_time', 0), reverse=True)
            
            # Формируем отчет
            report_parts = [
                "📈 <b>Активность за последние 24 часа</b>\n"
            ]
            
            # Топ-5 монет по длительности активности
            coin_durations = {}
            for session in all_sessions:
                symbol = session.get('symbol', '')
                duration = session.get('total_duration', 0)
                if symbol in coin_durations:
                    coin_durations[symbol] += duration
                else:
                    coin_durations[symbol] = duration
            
            top_coins = sorted(coin_durations.items(), key=lambda x: x[1], reverse=True)[:5]
            
            if top_coins:
                report_parts.append("🏆 <b>Топ-5 монет по активности:</b>")
                for i, (symbol, duration) in enumerate(top_coins, 1):
                    report_parts.append(f"{i}. <b>{symbol}</b> - {duration/60:.1f} мин")
                report_parts.append("")
            
            # Последние сессии, группированные по часам с уровнем активности (московское время UTC+3)
            recent_sessions = all_sessions[:40]  # Берем больше для анализа
            if recent_sessions:
                from activity_level_calculator import activity_calculator
                
                report_parts.append("🕐 <b>Последние сессии по часам:</b>")
                
                # Группируем по часам
                sessions_by_hour = {}
                for session in recent_sessions:
                    start_time = session.get('start_time', 0)
                    # Конвертируем в московское время (UTC+3)
                    moscow_time = datetime.fromtimestamp(start_time + 3*3600)
                    hour_key = moscow_time.strftime('%H:00')
                    hour_datetime = moscow_time.replace(minute=0, second=0, microsecond=0)
                    
                    if hour_key not in sessions_by_hour:
                        sessions_by_hour[hour_key] = {
                            'sessions': [],
                            'hour_datetime': hour_datetime
                        }
                    sessions_by_hour[hour_key]['sessions'].append(session)
                
                # Отображаем по часам (сортируем в обратном порядке)
                for hour in sorted(sessions_by_hour.keys(), reverse=True):
                    hour_data = sessions_by_hour[hour]
                    hour_sessions = hour_data['sessions']
                    hour_datetime = hour_data['hour_datetime']
                    
                    # Рассчитываем уровень активности для этого часа
                    # Сумма длительностей всех сессий в минутах
                    total_activity = sum(session.get('total_duration', 0) / 60 for session in hour_sessions)
                    
                    # Получаем информацию об уровне активности
                    activity_info = activity_calculator.get_activity_level_info(total_activity)
                    
                    # Обновляем статистику каждый час (если час завершился)
                    current_hour = datetime.now().replace(minute=0, second=0, microsecond=0)
                    moscow_current_hour = (datetime.now() + timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
                    
                    # Проверяем, завершился ли час по московскому времени
                    if hour_datetime < moscow_current_hour and total_activity > 0:
                        # Создаем уникальный ключ для часа
                        hour_key = f"{hour_datetime.strftime('%Y-%m-%d_%H')}"
                        activity_calculator.update_activity_stats(total_activity, hour_key)
                    
                    # Формируем заголовок часа с уровнем активности
                    if total_activity > 0:
                        session_count = len(hour_sessions)
                        avg_session_duration = total_activity / session_count if session_count > 0 else 0
                        
                        z_score_text = f" (z={activity_info['z_score']:.1f})" if activity_info['count'] > 1 else ""
                        report_parts.append(
                            f"\n<b>{hour}</b> {activity_info['color']} {activity_info['emoji']} "
                            f"<i>{activity_info['level']}</i>"
                        )
                        report_parts.append(
                            f"<i>Активность: {total_activity:.1f} мин ({session_count} сессий, "
                            f"ср. {avg_session_duration:.1f}м){z_score_text}</i>"
                        )
                        
                        # Группируем по монетам и суммируем их время
                        coin_activity = {}
                        for session in hour_sessions:
                            symbol = session.get('symbol', '')
                            duration = session.get('total_duration', 0) / 60
                            if symbol in coin_activity:
                                coin_activity[symbol] += duration
                            else:
                                coin_activity[symbol] = duration
                        
                        # Сортируем монеты по времени активности (убывание)
                        sorted_coins = sorted(coin_activity.items(), key=lambda x: x[1], reverse=True)
                        
                        # Показываем список монет с их суммарным временем
                        coins_text_parts = []
                        for symbol, duration in sorted_coins:
                            coins_text_parts.append(f"• {symbol} ({duration:.1f}м)")
                        
                        coins_text = "\n".join(coins_text_parts)
                        report_parts.append(f"Монеты:\n{coins_text}")
                        
                    else:
                        report_parts.append(f"\n<b>{hour}</b> ⚫ 💤 <i>Нет активности</i>")
                
                # Добавляем сводку статистики активности
                if activity_calculator.count > 0:
                    stats = activity_calculator.get_stats_summary()
                    report_parts.append(
                        f"\n📊 <b>Статистика активности:</b>\n"
                        f"• Среднее: <code>{stats['mean']:.1f} мин/час</code>\n"
                        f"• Стд. откл.: <code>{stats['std_dev']:.1f} мин</code>\n"
                        f"• Выборка: <code>{stats['count']} часов</code>"
                    )
                else:
                    report_parts.append(
                        f"\n📊 <b>Статистика активности:</b>\n"
                        f"• Недостаточно данных для анализа\n"
                        f"• Собираем статистику по часам..."
                    )
            
            # Разбиваем сообщение на части если слишком длинное
            report_text = "\n".join(report_parts)
            max_length = 4000
            
            if len(report_text) <= max_length:
                await update.message.reply_text(
                    report_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.main_keyboard
                )
            else:
                # Разбиваем на части
                parts = []
                current_part = []
                current_length = 0
                
                for line in report_parts:
                    line_length = len(line) + 1  # +1 для \n
                    if current_length + line_length > max_length and current_part:
                        parts.append("\n".join(current_part))
                        current_part = [line]
                        current_length = line_length
                    else:
                        current_part.append(line)
                        current_length += line_length
                
                if current_part:
                    parts.append("\n".join(current_part))
                
                # Отправляем части
                for i, part in enumerate(parts):
                    reply_markup = self.main_keyboard if i == len(parts) - 1 else None
                    await update.message.reply_text(
                        part,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup
                    )
                    if i < len(parts) - 1:
                        await asyncio.sleep(0.5)  # Небольшая пауза между сообщениями
            
        except Exception as e:
            bot_logger.error(f"Ошибка получения активности за 24ч: {e}")
            await update.message.reply_text(
                "❌ Ошибка получения данных об активности.",
                reply_markup=self.main_keyboard
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

        # Обработка кнопки "Назад"
        if text == "🔙 Назад":
            await self._handle_back(update)
            return ConversationHandler.END

        text = text.upper()

        # Убираем префиксы команд
        if text.startswith('/ADD'):
            text = text[4:].strip()

        # Предварительная валидация символа
        if not input_validator.validate_symbol(text):
            await update.message.reply_text(
                "❌ <b>Неверный формат символа</b>\n\n"
                "Символ должен содержать только буквы и цифры (2-10 символов)\n\n"
                "💡 <b>Попробуйте еще раз:</b>\n"
                "• Введите корректный символ\n"
                "• Или нажмите '🔙 Назад' для выхода\n\n"
                "Примеры: <code>BTC</code>, <code>ETH</code>, <code>ADA</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=self.back_keyboard
            )
            return self.ADDING_COIN  # Продолжаем ждать ввод

        symbol = text.replace('USDT', '').replace('_', '')

        # Дополнительная валидация - проверяем на известные некорректные символы
        invalid_symbols = [
            'ADAD', 'XXXX', 'NULL', 'UNDEFINED', 'TEST', 'FAKE',
            'SCAM', '123', 'ABC', 'XYZ', 'QQQ', 'WWW', 'EEE'        ]

        if symbol in invalid_symbols or len(symbol) < 2 or len(symbol) > 10:
            await update.message.reply_text(
                f"❌ <b>Символ '{symbol}' недействителен</b>\n\n"
                "Пожалуйста, используйте корректные символы криптовалют.\n\n"
                "💡 <b>Попробуйте еще раз:</b>\n"
                "• Введите другой символ\n"
                "• Или нажмите '🔙 Назад' для выхода\n\n"
                "Примеры: <code>BTC</code>, <code>ETH</code>, <code>ADA</code>, <code>SOL</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=self.back_keyboard
            )
            return self.ADDING_COIN  # Продолжаем ждать ввод

        if watchlist_manager.contains(symbol):
            await update.message.reply_text(
                f"⚠️ Монета <b>{symbol}</b> уже в списке отслеживания",
                parse_mode=ParseMode.HTML,
                reply_markup=self.main_keyboard
            )
            return ConversationHandler.END

        # Проверяем существование монеты через API с улучшенной обработкой ошибок
        loading_msg = None
        try:
            loading_msg = await update.message.reply_text("🔍 Проверяю монету...")

            # Проверяем кеш сначала для ускорения
            from cache_manager import cache_manager
            cached_data = cache_manager.get_ticker_cache(symbol)
            if cached_data:
                ticker_data = cached_data
                bot_logger.debug(f"Использован кеш для {symbol}")
            else:
                # Используем таймаут для проверки API
                ticker_data = await asyncio.wait_for(
                    api_client.get_ticker_data(symbol), 
                    timeout=10.0
                )

            if not ticker_data:
                try:
                    await update.message.reply_text(
                        f"❌ <b>Монета '{symbol}' не найдена на MEXC</b>\n\n"
                        "• Проверьте правильность символа\n"
                        "• Поддерживаются только пары с USDT\n"
                        "• Убедитесь что монета торгуется на MEXC\n\n"
                        "💡 <b>Попробуйте еще раз:</b>\n"
                        "• Введите другой символ монеты\n"
                        "• Или нажмите '🔙 Назад' для выхода\n\n"
                        "Примеры: <code>BTC</code>, <code>ETH</code>, <code>ADA</code>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=self.back_keyboard
                    )
                except Exception:
                    await update.message.reply_text(
                        f"❌ <b>Монета '{symbol}' не найдена на MEXC</b>\n\n"
                        "💡 Попробуйте ввести другой символ или нажмите '🔙 Назад'",
                        parse_mode=ParseMode.HTML,
                        reply_markup=self.back_keyboard
                    )
                return self.ADDING_COIN  # Продолжаем ждать ввод

        except asyncio.TimeoutError:
            try:
                if loading_msg:
                    await loading_msg.delete()
            except:
                pass
            await update.message.reply_text(
                f"⏱️ <b>Таймаут проверки монеты '{symbol}'</b>\n\n"
                "API слишком медленно отвечает.\n\n"
                "💡 <b>Попробуйте:</b>\n"
                "• Ввести символ еще раз\n"
                "• Или нажать '🔙 Назад' для выхода",
                parse_mode=ParseMode.HTML,
                reply_markup=self.back_keyboard
            )
            return self.ADDING_COIN  # Продолжаем ждать ввод
        except Exception as e:
            error_msg = str(e).lower()
            try:
                if loading_msg:
                    await loading_msg.delete()
            except:
                pass

            if ("invalid symbol" in error_msg or "400" in error_msg or 
                "inline keyboard expected" in error_msg or "circuit breaker" in error_msg):
                await update.message.reply_text(
                    f"❌ <b>Символ '{symbol}' не существует</b>\n\n"
                    "Монета не найдена на бирже MEXC.\n\n"
                    "💡 <b>Попробуйте еще раз:</b>\n"
                    "• Введите другой символ монеты\n"
                    "• Или нажмите '🔙 Назад' для выхода\n\n"
                    "Примеры: <code>BTC</code>, <code>ETH</code>, <code>ADA</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.back_keyboard
                )
                return self.ADDING_COIN  # Возвращаемся в состояние ожидания ввода
            else:
                bot_logger.error(f"Ошибка проверки монеты {symbol}: {e}")
                await update.message.reply_text(
                    f"⚠️ <b>Временная ошибка при проверке '{symbol}'</b>\n\n"
                    "API временно недоступен.\n\n"
                    "💡 <b>Что делать:</b>\n"
                    "• Попробуйте ввести символ снова\n"
                    "• Или нажмите '🔙 Назад' для выхода",
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.back_keyboard
                )
                return self.ADDING_COIN  # Продолжаем ждать ввод

        # Добавляем в список
        if watchlist_manager.add(symbol):
            # Автоматически восстанавливаем все Circuit Breaker'ы при успешной операции
            try:
                from circuit_breaker import api_circuit_breakers
                reset_count = 0
                for name, cb in api_circuit_breakers.items():
                    if cb.state.value in ['open', 'half_open']:
                        cb.force_close()
                        reset_count += 1
                
                if reset_count > 0:
                    bot_logger.info(f"🔄 Автоматически восстановлено {reset_count} Circuit Breaker'ов после успешного добавления монеты")
            except Exception as e:
                bot_logger.debug(f"Ошибка автовосстановления Circuit Breakers: {e}")

            price = float(ticker_data.get('lastPrice', 0))
            await update.message.reply_text(
                f"✅ <b>Монета добавлена!</b>\n\n"
                f"📊 <b>{symbol}</b>\n"
                f"💰 Цена: <code>${price:.6f}</code>\n"
                f"📈 Всего в списке: <b>{watchlist_manager.size()}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=self.main_keyboard
            )
            bot_logger.info(f"Добавлена монета {symbol} по цене ${price:.6f}")
        else:
            await update.message.reply_text(
                f"❌ Ошибка добавления монеты <b>{symbol}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=self.main_keyboard
            )

        return ConversationHandler.END

    async def remove_coin_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик удаления монеты"""
        text = update.message.text.strip()

        # Обработка кнопки "Назад"
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

    async def _monitor_coins(self):
        """Мониторинг списка монет"""
        self.bot_running = True

        last_report_time = 0
        report_interval = 300  # 5 минут

        try:
            while self.bot_running:
                try:
                    current_time = time.time()

                    # Получаем данные с кешированием  
                    watchlist_symbols = watchlist_manager.get_symbols()

                    if not watchlist_symbols:
                        bot_logger.warning("Список для мониторинга пуст")
                        await asyncio.sleep(10)
                        continue

                    # Анализируем данные для всех монет одновременно
                    coins_data = await self._get_all_coins_data(watchlist_symbols)

                    # Проверяем активность каждой монеты
                    for symbol in watchlist_symbols:
                        coin_data = coins_data.get(symbol)
                        if coin_data and data_validator.validate_coin_data(coin_data):
                            await self._check_coin_activity(symbol, coin_data)

                            # Обновляем данные в Session Recorder
                            try:
                                from session_recorder import session_recorder
                                session_recorder.update_coin_activity(symbol, coin_data)
                            except Exception as e:
                                bot_logger.debug(f"Ошибка обновления Session Recorder для {symbol}: {e}")

                    # Проверяем неактивные сессии в Session Recorder
                    try:
                        from session_recorder import session_recorder
                        session_recorder.check_inactive_sessions(self.active_coins)
                    except Exception as e:
                        bot_logger.debug(f"Ошибка проверки неактивных сессий: {e}")

                    # Очистка неактивных монет
                    await self._cleanup_inactive_coins()

                except Exception as e:
                    bot_logger.error(f"Ошибка мониторинга монет: {e}")
                    await asyncio.sleep(10)

                # Отправляем отчет раз в report_interval секунд
                if current_time - last_report_time >= report_interval:
                    await self._generate_and_send_report()
                    last_report_time = current_time

                await asyncio.sleep(5)

        except asyncio.CancelledError:
            bot_logger.info("Мониторинг монет остановлен")
        except Exception as e:
            bot_logger.error(f"Критическая ошибка в мониторинге монет: {e}")
        finally:
            self.bot_running = False

    async def _get_all_coins_data(self, symbols: List[str]) -> Dict[str, Dict]:
        """Получает данные сразу для всех монет"""
        tasks = [api_client.get_ticker_data(symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        coins_data = {}
        for i, symbol in enumerate(symbols):
            if isinstance(results[i], Exception):
                bot_logger.error(f"Ошибка получения данных для {symbol}: {results[i]}")
            else:
                coins_data[symbol] = results[i]
        return coins_data

    async def _check_coin_activity(self, symbol: str, coin_data: Dict):
        """Проверяет активность монеты и обрабатывает ее"""
        volume = float(coin_data.get('quoteVolume', 0))
        spread = data_validator.calculate_spread(coin_data)
        natr = await data_validator.calculate_natr(symbol)

        # Получаем пороги из конфига
        volume_threshold = config_manager.get('VOLUME_THRESHOLD')
        spread_threshold = config_manager.get('SPREAD_THRESHOLD')
        natr_threshold = config_manager.get('NATR_THRESHOLD')

        is_active = (
            volume >= volume_threshold and
            spread >= spread_threshold and
            natr >= natr_threshold
        )

        if is_active:
            if symbol not in self.active_coins:
                self.active_coins[symbol] = {
                    'last_active': time.time(),
                    'last_price': float(coin_data.get('lastPrice', 0)),
                    'highest_price': float(coin_data.get('lastPrice', 0)),
                    'lowest_price': float(coin_data.get('lastPrice', 0))
                }

                alert_text = (
                    f"🔥 <b>{symbol} Активна!</b>\n\n"
                    f"💰 Цена: <code>${self.active_coins[symbol]['last_price']:.6f}</code>\n"
                    f"📊 Объём: <code>${volume:,.2f}</code>\n"
                    f"⇄ Спред: <code>{spread:.2f}%</code>\n"
                    f"📈 NATR: <code>{natr:.2f}%</code>"
                )
                await self.send_message(alert_text)
                bot_logger.info(f"Обнаружена активная монета: {symbol}")
            else:
                # Обновляем last_active
                self.active_coins[symbol]['last_active'] = time.time()

                # Проверяем High/Low
                current_price = float(coin_data.get('lastPrice', 0))
                if current_price > self.active_coins[symbol]['highest_price']:
                    self.active_coins[symbol]['highest_price'] = current_price
                if current_price < self.active_coins[symbol]['lowest_price']:
                    self.active_coins[symbol]['lowest_price'] = current_price
        else:
            if symbol in self.active_coins:
                del self.active_coins[symbol]
                bot_logger.info(f"Монета {symbol} более не активна")

    async def _cleanup_inactive_coins(self):
        """Удаляет неактивные монеты из списка активных"""
        inactive_time = 300  # 5 минут
        current_time = time.time()
        inactive_coins = [
            symbol for symbol, data in self.active_coins.items()
            if current_time - data['last_active'] > inactive_time
        ]

        for symbol in inactive_coins:
            del self.active_coins[symbol]
            bot_logger.info(f"Удалена неактивная монета: {symbol}")

    async def _generate_and_send_report(self):
        """Генерирует и отправляет отчет о состоянии монет"""
        if not self.active_coins:
            bot_logger.debug("Нет активных монет для отчета")
            return

        report_parts = ["📊 <b>Отчет об активных монетах:</b>\n"]

        total_volume = 0
        for symbol, data in self.active_coins.items():
            volume = float(await api_client.get_quote_volume(symbol))
            total_volume += volume
            price_change = data['last_price'] - data['lowest_price']
            report_parts.append(
                f"• <b>{symbol}</b>: <code>${data['last_price']:.6f}</code> "
                f"(<code>+${price_change:.6f}</code>)\n"
                f"  Min: <code>${data['lowest_price']:.6f}</code> "
                f"Max: <code>${data['highest_price']:.6f}</code>"
            )

        report_parts.append(f"\n💰 Общий объём: <code>${total_volume:,.2f}</code>")
        report_text = "\n".join(report_parts)

        await self.send_message(report_text)
        bot_logger.info("Отправлен отчет об активных монетах")

    async def _queue_message(self, message_data: Dict[str, Any]):
        """Добавляет сообщение в очередь для отправки"""
        try:
            if self._message_queue is None:
                self._message_queue = asyncio.Queue()

            await self._message_queue.put(message_data)
            bot_logger.debug("📤 Сообщение добавлено в очередь")
        except Exception as e:
            bot_logger.error(f"Ошибка добавления в очередь: {e}")

    def setup_application(self):
        """Настраивает Telegram приложение"""
        from telegram.error import Conflict, NetworkError, TimedOut

        builder = Application.builder()
        builder.token(self.token)
        builder.connection_pool_size(4)
        builder.pool_timeout(15.0)
        builder.read_timeout(20.0)
        builder.write_timeout(20.0)

        async def error_handler(update, context):
            error = context.error

            if isinstance(error, Conflict):
                bot_logger.warning("Конфликт Telegram API - возможно запущен другой экземпляр бота")
                return
            elif isinstance(error, (NetworkError, TimedOut)):
                bot_logger.debug(f"Сетевая ошибка Telegram: {type(error).__name__}")
                return
            else:
                bot_logger.error(f"Ошибка Telegram бота: {error}")

        self.app = builder.build()
        self.app.add_error_handler(error_handler)

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

        self.app.add_handler(CommandHandler("start", self.start_handler))
        self.app.add_handler(conv_handler)

        return self.app

# Глобальный экземпляр бота
telegram_bot = TradingTelegramBot()