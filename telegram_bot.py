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
from user_manager import user_manager
from user_session_recorder import UserSessionRecorder
from admin_handlers import create_admin_handlers
from user_modes_manager import UserModesManager
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

        # Модули режимов (для админа - обратная совместимость)
        self.notification_mode = NotificationMode(self)
        self.monitoring_mode = MonitoringMode(self)

        # Менеджер персональных режимов пользователей
        self.user_modes_manager = UserModesManager(self)

        # Многопользовательские модули
        self.admin_handlers = create_admin_handlers(self)
        self.user_session_recorders: Dict[str, UserSessionRecorder] = {}

        # Состояния ConversationHandler
        self.ADDING_COIN, self.REMOVING_COIN = range(2)
        self.SETTING_VOLUME, self.SETTING_SPREAD, self.SETTING_NATR = range(2, 5)

        self._setup_keyboards()

    @property
    def active_coins(self):
        """Свойство для обратной совместимости с основным health check"""
        # Для админа показываем его персональные активные монеты или глобальные
        admin_mode = self.user_modes_manager.get_user_mode(self.chat_id)
        if admin_mode == 'notification':
            admin_stats = self.user_modes_manager.get_user_stats(self.chat_id)
            mode_stats = admin_stats.get('modes', {}).get('notification', {})
            active_coins = mode_stats.get('active_coins', [])
            # Преобразуем в формат {symbol: {}} для совместимости
            return {coin: {'active': True} for coin in active_coins}
        elif self.bot_mode == 'notification':
            return self.notification_mode.active_coins
        return {}

    def get_user_keyboard(self, chat_id: str) -> ReplyKeyboardMarkup:
        """Возвращает соответствующую клавиатуру для пользователя"""
        if user_manager.is_admin(chat_id):
            return self.admin_keyboard
        else:
            return self.user_keyboard

    def get_user_session_recorder(self, chat_id: str) -> UserSessionRecorder:
        """Получает или создает сессионный рекордер для пользователя"""
        chat_id_str = str(chat_id)
        if chat_id_str not in self.user_session_recorders:
            self.user_session_recorders[chat_id_str] = UserSessionRecorder(chat_id_str)
        return self.user_session_recorders[chat_id_str]

    def get_user_watchlist(self, chat_id: str) -> List[str]:
        """Получает список монет пользователя"""
        if user_manager.is_admin(chat_id):
            # Для админа используем глобальный список
            return watchlist_manager.get_all()
        else:
            # Для пользователя используем его личный список
            return user_manager.get_user_watchlist(chat_id)

    def get_user_config(self, chat_id: str) -> Dict:
        """Получает конфигурацию пользователя"""
        if user_manager.is_admin(chat_id):
            # Для админа используем глобальную конфигурацию
            return {
                'VOLUME_THRESHOLD': config_manager.get('VOLUME_THRESHOLD'),
                'SPREAD_THRESHOLD': config_manager.get('SPREAD_THRESHOLD'),
                'NATR_THRESHOLD': config_manager.get('NATR_THRESHOLD')
            }
        else:
            # Для пользователя используем его личную конфигурацию
            return user_manager.get_user_config(chat_id)

    def _setup_keyboards(self):
        """Настраивает клавиатуры"""
        # Клавиатура администратора
        self.admin_keyboard = ReplyKeyboardMarkup([
            ["🔔 Уведомления", "📊 Мониторинг"],
            ["➕ Добавить", "➖ Удалить"],
            ["📋 Список", "⚙ Настройки"],
            ["📈 Активность 24ч", "ℹ Статус"],
            ["👥 Список заявок", "📋 Логи"],
            ["👤 Управление пользователями", "🛑 Стоп", "🧹 Очистить пользователей"]
        ], resize_keyboard=True, one_time_keyboard=False)

        # Клавиатура обычного пользователя
        self.user_keyboard = ReplyKeyboardMarkup([
            ["🔔 Уведомления", "📊 Мониторинг"],
            ["➕ Добавить", "➖ Удалить"],
            ["📋 Список", "⚙ Настройки"],
            ["📈 Активность 24ч", "ℹ Статус"],
            ["🛑 Стоп"]
        ], resize_keyboard=True, one_time_keyboard=False)

        # Основная клавиатура (используется по умолчанию)
        self.main_keyboard = self.admin_keyboard

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
        chat_id = update.effective_chat.id
        user = update.effective_user

        # Проверяем, является ли пользователь администратором
        if user_manager.is_admin(chat_id):
            await self._handle_admin_start(update, context)
            return

        # Проверяем, одобрен ли пользователь
        if user_manager.is_user_approved(chat_id):
            return await self._handle_approved_user_start(update, context)

        # Проверяем, есть ли уже заявка от этого пользователя
        if user_manager.is_user_pending(chat_id):
            await update.message.reply_text(
                "⏳ <b>Ваша заявка уже отправлена</b>\n\n"
                "Пожалуйста, ожидайте решения администратора.\n"
                "Вы получите уведомление, как только заявка будет рассмотрена.",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END

        # Новый пользователь - создаем заявку
        user_info = {
            'username': user.username or 'no_username',
            'first_name': user.first_name or 'Unknown',
            'last_name': user.last_name or ''
        }

        if user_manager.add_pending_request(chat_id, user_info):
            await update.message.reply_text(
                "👋 <b>Добро пожаловать в MEXCScalping Assistant!</b>\n\n"
                "📝 Ваша заявка на подключение отправлена администратору.\n\n"
                "⏳ <b>Ожидайте одобрения</b>\n"
                "Вы получите уведомление, как только администратор рассмотрит вашу заявку.\n\n"
                "💡 Обычно это занимает несколько минут.",
                parse_mode=ParseMode.HTML
            )

            # Уведомляем администратора о новой заявке
            try:
                await self.app.bot.send_message(
                    chat_id=user_manager.admin_chat_id,
                    text=(
                        f"🔔 <b>Новая заявка на подключение!</b>\n\n"
                        f"👤 <b>{user_info['first_name']}</b>\n"
                        f"• Username: @{user_info['username']}\n"
                        f"• ID: <code>{chat_id}</code>\n\n"
                        f"Нажмите '👥 Список заявок' для обработки"
                    ),
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                bot_logger.error(f"Ошибка уведомления админа о новой заявке: {e}")

        else:
            await update.message.reply_text(
                "❌ Ошибка при отправке заявки. Попробуйте позже.",
                parse_mode=ParseMode.HTML
            )

    async def _handle_admin_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка старта для администратора"""
        last_mode = bot_state_manager.get_last_mode()

        welcome_text = (
            "🤖 <b>Добро пожаловать, Администратор!</b>\n\n"
            "📊 <b>Режимы работы:</b>\n"
            "• 🔔 <b>Уведомления</b> - оповещения об активных монетах\n"
            "• 📊 <b>Мониторинг</b> - постоянное отслеживание списка\n\n"
            "👥 <b>Администрирование:</b>\n"
            "• 👥 Список заявок - управление новыми пользователями\n"
            "• 📋 Логи - просмотр системных логов\n"
            "• 👤 Управление пользователями\n\n"
        )

        # Автовосстановление последнего режима для админа
        if last_mode and not self.bot_running:
            welcome_text += f"🔄 <b>Восстанавливаю режим {last_mode}...</b>\n\n"
            await update.message.reply_text(welcome_text + "Выберите действие:", reply_markup=self.admin_keyboard, parse_mode=ParseMode.HTML)

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
        await update.message.reply_text(welcome_text, reply_markup=self.admin_keyboard, parse_mode=ParseMode.HTML)

    async def _handle_approved_user_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка старта для одобренного пользователя"""
        chat_id = update.effective_chat.id
        user_watchlist = user_manager.get_user_watchlist(chat_id)

        # Всегда показываем пользователю меню с кнопками
        welcome_text = (
            "🤖 <b>Добро пожаловать в MEXCScalping Assistant!</b>\n\n"
            "📊 <b>Ваши режимы работы:</b>\n"
            "• 🔔 <b>Уведомления</b> - персональные оповещения\n"
            "• 📊 <b>Мониторинг</b> - отслеживание ваших монет\n\n"
            "⚙ <b>Управление:</b>\n"
            "• ➕ Добавить монету в ваш список\n"
            "• ➖ Удалить монету из списка\n"
            "• 📋 Показать ваши монеты\n"
            "• ⚙ Ваши настройки фильтров\n\n"
        )

        # Если нет монет, добавляем напоминание
        if not user_watchlist:
            welcome_text += (
                "⚠️ <b>Важно:</b> У вас нет монет для отслеживания!\n"
                "Нажмите ➕ <b>Добавить</b> чтобы добавить первую монету.\n\n"
            )
        else:
            welcome_text += f"📋 <b>Ваши монеты:</b> {len(user_watchlist)} шт.\n\n"

        welcome_text += "Выберите действие:"

        await update.message.reply_text(
            welcome_text, 
            reply_markup=self.user_keyboard, 
            parse_mode=ParseMode.HTML
        )
        
        # Отмечаем настройку как завершенную и очищаем состояние
        user_manager.mark_setup_completed(chat_id)
        user_manager.update_user_data(chat_id, {'setup_state': 'completed'})
        return ConversationHandler.END

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Основной обработчик кнопок с защитой от spam"""
        text = update.message.text
        chat_id = update.effective_chat.id
        current_time = time.time()

        # Проверяем права доступа
        if not user_manager.is_admin(chat_id) and not user_manager.is_user_approved(chat_id):
            await update.message.reply_text(
                "❌ У вас нет доступа к боту.\nОтправьте /start для подачи заявки.",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END

        # Проверяем, находится ли пользователь в процессе первоначальной настройки
        if user_manager.is_user_approved(chat_id) and not user_manager.is_setup_completed(chat_id):
            user_data = user_manager.get_user_data(chat_id)
            setup_state = user_data.get('setup_state', '') if user_data else ''

            # Обрабатываем ввод монеты только если пользователь в состоянии добавления монет
            if setup_state == 'initial_coin_setup':
                # Игнорируем команды и обрабатываем любой другой текст как монету
                if not text.startswith('/') and text not in ['🔔 Уведомления', '📊 Мониторинг', '➕ Добавить', '➖ Удалить', 
                                                           '📋 Список', '⚙ Настройки', '📈 Активность 24ч', 'ℹ Статус', '🛑 Стоп']:
                    return await self._handle_initial_coin_input(update, text)
                else:
                    await update.message.reply_text(
                        "💡 <b>Сначала добавьте монету!</b>\n\n"
                        "Введите название монеты для добавления в ваш список.\n\n"
                        "Например: BTC, ETH, ADA, SOL",
                        parse_mode=ParseMode.HTML
                    )
                    return ConversationHandler.END
            elif setup_state.startswith('setting_filters'):
                return await self._handle_initial_filter_input(update, text)
            elif setup_state == 'coin_added_waiting_choice':
                # Пользователь добавил монету и ждет выбора действия через inline кнопки
                await update.message.reply_text(
                    "💡 <b>Используйте кнопки выше для выбора действия:</b>\n\n"
                    "• ➕ Добавить еще монету\n"
                    "• ⚙️ Перейти к настройкам",
                    parse_mode=ParseMode.HTML
                )
                return ConversationHandler.END

        # Защита от spam нажатий (минимум 1 секунда между операциями)
        if current_time - self._last_operation_time < 1.0:
            bot_logger.debug("Слишком быстрые нажатия, игнорируем")
            return ConversationHandler.END

        self._last_operation_time = current_time
        user_keyboard = self.get_user_keyboard(chat_id)

        # Дополнительная проверка для пользователей без монет (после завершения настройки)
        if (user_manager.is_user_approved(chat_id) and 
            user_manager.is_setup_completed(chat_id) and 
            not user_manager.get_user_watchlist(chat_id) and
            text not in ["➕ Добавить", "⚙ Настройки", "ℹ Статус", "🛑 Стоп"]):
            
            await update.message.reply_text(
                "⚠️ <b>У вас нет монет для отслеживания!</b>\n\n"
                "Для использования этой функции сначала добавьте хотя бы одну монету.\n\n"
                "Нажмите ➕ <b>Добавить</b> для добавления монеты.",
                reply_markup=user_keyboard,
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END

        try:
            # Проверяем, не идет ли уже переключение режима
            if self._switching_mode:
                await update.message.reply_text(
                    "⏳ Идет переключение режима, подождите...",
                    reply_markup=user_keyboard
                )
                return ConversationHandler.END

            message_text = update.message.text

            # Админские функции
            if message_text == "👥 Список заявок":
                await self.admin_handlers.handle_pending_requests(update, context)
            elif message_text == "📋 Логи":
                await self.admin_handlers.handle_logs_request(update, context)
            elif message_text == "👤 Управление пользователями":
                await self.admin_handlers.handle_user_management(update, context)
            elif message_text == "🧹 Очистить пользователей":
                await self.admin_handlers.handle_clear_all_users(update, context)
                return ConversationHandler.END  # Добавляем return чтобы не попасть в обработку общих кнопок

            # Общие кнопки для всех пользователей
            elif text == "🔔 Уведомления":
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
                    reply_markup=user_keyboard
                )
        except Exception as e:
            bot_logger.error(f"Ошибка в button_handler: {e}", exc_info=True)
            try:
                await update.message.reply_text(
                    "❌ Произошла ошибка. Попробуйте еще раз через несколько секунд.",
                    reply_markup=user_keyboard
                )
            except Exception as reply_error:
                bot_logger.error(f"Не удалось отправить сообщение об ошибке: {reply_error}")

        return ConversationHandler.END

    async def _handle_notification_mode(self, update: Update):
        """Обработка режима уведомлений"""
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

        # Проверяем наличие монет у пользователя
        user_watchlist = self.get_user_watchlist(chat_id)
        if not user_watchlist:
            await update.message.reply_text(
                "⚠️ <b>Нет монет для отслеживания!</b>\n\n"
                "Чтобы запустить режим уведомлений, сначала добавьте хотя бы одну монету.\n\n"
                "Нажмите ➕ <b>Добавить</b> для добавления монеты.",
                reply_markup=user_keyboard,
                parse_mode="HTML"
            )
            return

        # Проверяем текущий режим пользователя
        current_mode = self.user_modes_manager.get_user_mode(chat_id)
        if current_mode == 'notification':
            await update.message.reply_text(
                "✅ У вас уже активен режим уведомлений.",
                reply_markup=user_keyboard
            )
            return

        # Для админа также используем персональный режим
        if user_manager.is_admin(chat_id):
            # Останавливаем старые глобальные режимы для совместимости
            await self._stop_current_mode()

        # Запускаем персональный режим уведомлений
        success = await self.user_modes_manager.start_user_mode(chat_id, 'notification')

        if success:
            await update.message.reply_text(
                "✅ <b>Персональный режим уведомлений активирован</b>\n"
                "🔔 Вы будете получать уведомления об активных монетах из вашего списка.",
                reply_markup=user_keyboard,
                parse_mode="HTML"
            )
            bot_logger.info(f"🔔 Режим уведомлений активирован для пользователя {chat_id}")
        else:
            await update.message.reply_text(
                "❌ Ошибка запуска режима уведомлений. Попробуйте позже.",
                reply_markup=user_keyboard
            )

    async def _handle_monitoring_mode(self, update: Update):
        """Обработка режима мониторинга"""
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

        # Проверяем наличие монет у пользователя
        user_watchlist = self.get_user_watchlist(chat_id)
        if not user_watchlist:
            await update.message.reply_text(
                "⚠️ <b>Нет монет для отслеживания!</b>\n\n"
                "Чтобы запустить режим мониторинга, сначала добавьте хотя бы одну монету.\n\n"
                "Нажмите ➕ <b>Добавить</b> для добавления монеты.",
                reply_markup=user_keyboard,
                parse_mode="HTML"
            )
            return

        # Проверяем текущий режим пользователя
        current_mode = self.user_modes_manager.get_user_mode(chat_id)
        if current_mode == 'monitoring':
            await update.message.reply_text(
                "✅ У вас уже активен режим мониторинга.",
                reply_markup=user_keyboard
            )
            return

        # Для админа также используем персональный режим
        if user_manager.is_admin(chat_id):
            # Останавливаем старые глобальные режимы для совместимости
            await self._stop_current_mode()

        # Запускаем персональный режим мониторинга
        success = await self.user_modes_manager.start_user_mode(chat_id, 'monitoring')

        if success:
            await update.message.reply_text(
                "✅ <b>Персональный режим мониторинга активирован</b>\n"
                "📊 Сводка по вашим монетам будет обновляться автоматически.",
                reply_markup=user_keyboard,
                parse_mode="HTML"
            )
            bot_logger.info(f"📊 Режим мониторинга активирован для пользователя {chat_id}")
        else:
            await update.message.reply_text(
                "❌ Ошибка запуска режима мониторинга. Попробуйте позже.",
                reply_markup=user_keyboard
            )

    async def _handle_stop(self, update: Update):
        """Обработка остановки бота"""
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

        # Останавливаем персональный режим пользователя
        stopped = await self.user_modes_manager.stop_user_mode(chat_id)

        # Для админа также останавливаем глобальные режимы если есть
        if user_manager.is_admin(chat_id):
            await self._stop_current_mode()

        if stopped or (user_manager.is_admin(chat_id) and self.bot_running):
            await update.message.reply_text(
                "🛑 <b>Ваши режимы остановлены</b>",
                reply_markup=user_keyboard,
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                "ℹ️ У вас нет активных режимов.",
                reply_markup=user_keyboard
            )

    async def _handle_add_coin_start(self, update: Update):
        """Начало добавления монеты"""
        chat_id = update.effective_chat.id

        # Проверяем права доступа
        if not user_manager.is_admin(chat_id) and not user_manager.is_user_approved(chat_id):
            await update.message.reply_text(
                "❌ У вас нет доступа к боту.",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END

        await update.message.reply_text(
            "➕ <b>Добавление монеты</b>\n\n"
            "Введите символ монеты (например: <code>BTC</code> или <code>BTC_USDT</code>):",
            reply_markup=self.back_keyboard,
            parse_mode=ParseMode.HTML
        )
        return self.ADDING_COIN

    async def _handle_remove_coin_start(self, update: Update):
        """Начало удаления монеты"""
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

        # Проверяем права доступа
        if not user_manager.is_admin(chat_id) and not user_manager.is_user_approved(chat_id):
            await update.message.reply_text(
                "❌ У вас нет доступа к боту.",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END

        # Получаем список монет в зависимости от роли
        if user_manager.is_admin(chat_id):
            coins = watchlist_manager.get_all()
            list_title = "админский список"
        else:
            coins = user_manager.get_user_watchlist(chat_id)
            list_title = "ваш список"

        if len(coins) == 0:
            await update.message.reply_text(
                f"❌ {list_title.capitalize()} отслеживания пуст.",
                reply_markup=user_keyboard
            )
            return ConversationHandler.END

        coins_list = ", ".join(sorted(coins)[:10])
        if len(coins) > 10:
            coins_list += "..."

        await update.message.reply_text(
            f"➖ <b>Удаление монеты</b>\n\n"
            f"Текущий {list_title}: {coins_list}\n\n"
            f"Введите символ монеты для удаления:",
            reply_markup=self.back_keyboard,
            parse_mode=ParseMode.HTML
        )
        return self.REMOVING_COIN

    async def _handle_volume_setting_start(self, update: Update):
        """Начало настройки объёма"""
        chat_id = update.effective_chat.id

        # Получаем текущее значение в зависимости от роли
        if user_manager.is_admin(chat_id):
            current_value = config_manager.get('VOLUME_THRESHOLD')
        else:
            user_config = user_manager.get_user_config(chat_id)
            current_value = user_config.get('VOLUME_THRESHOLD', 1000)

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
        chat_id = update.effective_chat.id

        # Получаем текущее значение в зависимости от роли
        if user_manager.is_admin(chat_id):
            current_value = config_manager.get('SPREAD_THRESHOLD')
        else:
            user_config = user_manager.get_user_config(chat_id)
            current_value = user_config.get('SPREAD_THRESHOLD', 0.1)

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
        chat_id = update.effective_chat.id

        # Получаем текущее значение в зависимости от роли
        if user_manager.is_admin(chat_id):
            current_value = config_manager.get('NATR_THRESHOLD')
        else:
            user_config = user_manager.get_user_config(chat_id)
            current_value = user_config.get('NATR_THRESHOLD', 0.5)

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
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

        # Получаем список монет в зависимости от роли
        if user_manager.is_admin(chat_id):
            coins = watchlist_manager.get_all()
            list_title = "📋 <b>Админский список отслеживания"
        else:
            coins = user_manager.get_user_watchlist(chat_id)
            list_title = "📋 <b>Ваш список отслеживания"

        if not coins:
            text = f"{list_title} пуст</b>"
        else:
            sorted_coins = sorted(coins)
            text = f"{list_title} ({len(coins)} монет):</b>\n\n"

            for i in range(0, len(sorted_coins), 5):
                batch = sorted_coins[i:i+5]
                text += " • ".join(batch) + "\n"

        await update.message.reply_text(text, reply_markup=user_keyboard, parse_mode=ParseMode.HTML)

    async def _handle_settings(self, update: Update):
        """Обработка настроек"""
        chat_id = update.effective_chat.id

        # Получаем настройки в зависимости от роли
        if user_manager.is_admin(chat_id):
            volume_threshold = config_manager.get('VOLUME_THRESHOLD')
            spread_threshold = config_manager.get('SPREAD_THRESHOLD')
            natr_threshold = config_manager.get('NATR_THRESHOLD')
            settings_title = "⚙ <b>Текущие настройки фильтров (админ):</b>\n\n"
        else:
            user_config = user_manager.get_user_config(chat_id)
            volume_threshold = user_config.get('VOLUME_THRESHOLD', 1000)
            spread_threshold = user_config.get('SPREAD_THRESHOLD', 0.1)
            natr_threshold = user_config.get('NATR_THRESHOLD', 0.5)
            settings_title = "⚙ <b>Ваши настройки фильтров:</b>\n\n"

        current_settings = (
            settings_title +
            f"📊 Минимальный объём: <code>${volume_threshold:,}</code>\n"
            f"⇄ Минимальный спред: <code>{spread_threshold}%</code>\n"
            f"📈 Минимальный NATR: <code>{natr_threshold}%</code>\n\n"
            "Выберите параметр для изменения:"
        )

        await update.message.reply_text(
            current_settings,
            reply_markup=self.settings_keyboard,
            parse_mode=ParseMode.HTML
        )

    async def _handle_status(self, update: Update):
        """Показ статуса бота"""
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

        status_parts = ["ℹ <b>Ваш статус:</b>\n"]

        # Проверяем персональный режим пользователя
        current_mode = self.user_modes_manager.get_user_mode(chat_id)
        user_stats = self.user_modes_manager.get_user_stats(chat_id)

        if current_mode:
            status_parts.append(f"🟢 Ваш режим: <b>{current_mode}</b>")

            mode_stats = user_stats.get('modes', {}).get(current_mode, {})

            if current_mode == 'notification':
                active_count = mode_stats.get('active_coins_count', 0)
                status_parts.append(f"📊 Активных монет: <b>{active_count}</b>")
                if mode_stats.get('active_coins'):
                    coins_list = ', '.join(mode_stats['active_coins'][:5])
                    status_parts.append(f"• Монеты: {coins_list}")

            elif current_mode == 'monitoring':
                watchlist_size = mode_stats.get('watchlist_size', 0)
                status_parts.append(f"📋 Отслеживается: <b>{watchlist_size}</b> монет")
        else:
            status_parts.append("🔴 Режимы остановлены")

        # Показываем личную статистику пользователя
        if user_manager.is_admin(chat_id):
            status_parts.append(f"\n📋 Глобальный список: <b>{watchlist_manager.size()}</b> монет")
            user_config = config_manager.get_all()
        else:
            user_watchlist = user_manager.get_user_watchlist(chat_id)
            status_parts.append(f"\n📋 Ваших монет: <b>{len(user_watchlist)}</b>")
            user_config = user_manager.get_user_config(chat_id)

        status_parts.append("\n⚙ <b>Ваши фильтры:</b>")
        status_parts.append(f"• Объём: ${user_config.get('VOLUME_THRESHOLD', 1000):,}")
        status_parts.append(f"• Спред: {user_config.get('SPREAD_THRESHOLD', 0.1)}%")
        status_parts.append(f"• NATR: {user_config.get('NATR_THRESHOLD', 0.5)}%")

        await update.message.reply_text(
            "\n".join(status_parts),
            reply_markup=user_keyboard,
            parse_mode=ParseMode.HTML
        )

    async def _handle_reset_settings(self, update: Update):
        """Сброс настроек к значениям по умолчанию"""
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

        # Сбрасываем настройки в зависимости от роли
        if user_manager.is_admin(chat_id):
            config_manager.set('VOLUME_THRESHOLD', 1000)
            config_manager.set('SPREAD_THRESHOLD', 0.1)
            config_manager.set('NATR_THRESHOLD', 0.5)
            settings_title = "🔄 <b>Админские настройки сброшены к значениям по умолчанию:</b>\n\n"
        else:
            user_manager.update_user_config(chat_id, 'VOLUME_THRESHOLD', 1000)
            user_manager.update_user_config(chat_id, 'SPREAD_THRESHOLD', 0.1)
            user_manager.update_user_config(chat_id, 'NATR_THRESHOLD', 0.5)
            settings_title = "🔄 <b>Ваши настройки сброшены к значениям по умолчанию:</b>\n\n"

        reset_message = (
            settings_title +
            f"📊 Минимальный объём: <code>$1,000</code>\n"
            f"⇄ Минимальный спред: <code>0.1%</code>\n"
            f"📈 Минимальный NATR: <code>0.5%</code>"
        )

        await update.message.reply_text(
            reset_message,
            reply_markup=user_keyboard,
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

            # Последние сессии, сгруппированные по часам с уровнем активности (московское время UTC+3)
            recent_sessions = all_sessions[:40]  # Берем больше для анализа
            if recent_sessions:
                from activity_level_calculator import activity_calculator

                report_parts.append("🕐 <b>Последние сессии по часам:</b>")

                # Группируем по часам
                sessions_by_hour = {}
                for session in recent_sessions:
                    start_time = session.get('start_time', 0)
                    # Преобразуем в московское время (UTC+3)
                    moscow_time = datetime.fromtimestamp(start_time) + timedelta(hours=3)
                    hour_key = moscow_time.strftime('%H:00')

                    if hour_key not in sessions_by_hour:
                        sessions_by_hour[hour_key] = []
                    sessions_by_hour[hour_key].append(session)

            # Определяем диапазон часов для отображения (последние 24 часа)
            now_moscow = datetime.now() + timedelta(hours=3)
            hours_to_show = []

            for i in range(24):  # Показываем последние 24 часа
                hour_dt = now_moscow - timedelta(hours=i)
                hour_str = hour_dt.strftime('%H:00')
                hour_key_stats = hour_dt.strftime("%Y-%m-%d_%H")

                # Получаем сессии для этого часа или пустой список
                hour_sessions = sessions_by_hour.get(hour_str, [])

                # Рассчитываем активность за час
                total_activity = activity_calculator.calculate_hourly_activity(hour_sessions, None)

                # Получаем информацию об уровне активности (БЕЗ обновления статистики)
                activity_info = activity_calculator.get_activity_level_info(total_activity)

                hours_to_show.append({
                    'hour': hour_str,
                    'hour_dt': hour_dt,
                    'sessions': hour_sessions,
                    'activity': total_activity,
                    'activity_info': activity_info
                })

            # Сортируем по времени (новые сначала) и показываем ВСЕ 24 часа
            hours_to_show.sort(key=lambda x: x['hour_dt'], reverse=True)

            for hour_data in hours_to_show:  # Показываем все 24 часа
                hour = hour_data['hour']
                hour_sessions = hour_data['sessions']
                total_activity = hour_data['activity']
                activity_info = hour_data['activity_info']

                report_parts.append(f"\n{hour} {activity_info['color']} {activity_info['emoji']} {activity_info['level']}")

                if hour_sessions:
                    report_parts.append(f"Активность: {total_activity:.1f} мин ({len(hour_sessions)} сессий, ср. {total_activity/len(hour_sessions):.1f}м) (z={activity_info['z_score']:.1f})")

                    # Группируем монеты по длительности
                    coin_durations_hour = {}
                    for session in hour_sessions:
                        symbol = session.get('symbol', '')
                        duration = session.get('total_duration', 0) / 60  # В минутах
                        if symbol in coin_durations_hour:
                            coin_durations_hour[symbol] += duration
                        else:
                            coin_durations_hour[symbol] = duration

                    # Сортируем монеты по активности
                    top_coins_hour = sorted(coin_durations_hour.items(), key=lambda x: x[1], reverse=True)

                    if top_coins_hour:
                        coins_text = []
                        for symbol, duration in top_coins_hour[:10]:  # Топ-10 монет
                            coins_text.append(f"• {symbol} ({duration:.1f}м)")
                        report_parts.append("Монеты:")
                        report_parts.extend(coins_text)
                else:
                    report_parts.append(f"Активность: 0.0 мин (0 сессий) (z={activity_info['z_score']:.1f})")
                    report_parts.append("Монеты: нет активности")

            # Добавляем статистику активности с правильными расчетами
            from activity_level_calculator import activity_calculator

            # Получаем все активности за 24 часа для корректной статистики
            all_24h_activities = activity_calculator.get_last_24_hours_activity()
            stats_24h = activity_calculator.calculate_activity_statistics_welford(all_24h_activities)

            report_parts.append("")
            report_parts.append("📊 <b>Статистика активности:</b>")
            report_parts.append(f"• Среднее: {stats_24h['mean']:.1f} мин/час")
            report_parts.append(f"• Стд. откл.: {stats_24h['std']:.1f} мин")
            report_parts.append(f"• Выборка: {stats_24h['count']} часов")

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

        # Проверяем дублирование в соответствующем списке
        chat_id = update.effective_chat.id
        
        if user_manager.is_admin(chat_id):
            # Для админа проверяем глобальный список
            if watchlist_manager.contains(symbol):
                await update.message.reply_text(
                    f"⚠️ Монета <b>{symbol}</b> уже в глобальном списке отслеживания",
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.main_keyboard
                )
                return ConversationHandler.END
        else:
            # Для пользователя проверяем его личный список
            user_watchlist = user_manager.get_user_watchlist(chat_id)
            if symbol in user_watchlist:
                await update.message.reply_text(
                    f"⚠️ Монета <b>{symbol}</b> уже в вашем списке отслеживания",
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

        # Добавляем в список (админ - глобальный, пользователь - личный)
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

        success = False
        total_count = 0

        if user_manager.is_admin(chat_id):
            # Админ добавляет в глобальный список
            success = watchlist_manager.add(symbol)
            total_count = watchlist_manager.size()
        else:
            # Пользователь добавляет в свой список
            success = user_manager.add_user_coin(chat_id, symbol)
            total_count = len(user_manager.get_user_watchlist(chat_id))

        if success:
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
            # Отправляем подтверждение
            await update.message.reply_text(
                f"✅ <b>Монета добавлена!</b>\n\n"
                f"📊 <b>{symbol}</b>\n"
                f"💰 Цена: ${price:.6f}\n"
                f"📈 Всего монет: {len(user_watchlist)}\n\n"
                f"🔄 <b>Что дальше?</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=user_keyboard
            )

            # Если это первая монета пользователя, завершаем настройку
            if len(user_watchlist) == 1 and not user_manager.is_setup_completed(chat_id):
                user_manager.mark_setup_completed(chat_id)
                await asyncio.sleep(1)  # Небольшая пауза
                await update.message.reply_text(
                    "🎉 <b>Настройка завершена!</b>\n\n"
                    "Теперь вы можете:\n"
                    "• 🔔 Запустить режим уведомлений\n"
                    "• 📊 Включить мониторинг списка\n"
                    "• ➕ Добавить еще монеты\n"
                    "• ⚙ Настроить фильтры\n\n"
                    "Выберите действие из меню! 👇",
                    parse_mode=ParseMode.HTML,
                    reply_markup=user_keyboard
                )
            bot_logger.info(f"Добавлена монета {symbol} по цене ${price:.6f} {'(админ)' if user_manager.is_admin(chat_id) else '(пользователь)'}")
        else:
            await update.message.reply_text(
                f"❌ Ошибка добавления монеты <b>{symbol}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=user_keyboard
            )

        return ConversationHandler.END

    async def remove_coin_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик удаления монеты"""
        text = update.message.text.strip()
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

        # Обработка кнопки "Назад"
        if text == "🔙 Назад":
            await update.message.reply_text(
                "🏠 Главное меню:",
                reply_markup=user_keyboard
            )
            return ConversationHandler.END

        symbol = text.upper().replace("_USDT", "").replace("USDT", "")

        success = False
        if user_manager.is_admin(chat_id):
            # Админ удаляет из глобального списка
            success = watchlist_manager.remove(symbol)
        else:
            # Пользователь удаляет из своего списка
            success = user_manager.remove_user_coin(chat_id, symbol)

        if success:
            await update.message.reply_text(
                f"✅ <b>{symbol}</b> удалена из списка отслеживания.",
                reply_markup=user_keyboard,
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                f"❌ <b>{symbol}</b> не найдена в списке.",
                reply_markup=user_keyboard,
                parse_mode=ParseMode.HTML
            )

        return ConversationHandler.END

    async def volume_setting_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик настройки объёма"""
        text = update.message.text.strip()
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

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

            # Сохраняем настройки в зависимости от роли
            if user_manager.is_admin(chat_id):
                config_manager.set('VOLUME_THRESHOLD', value)
            else:
                user_manager.update_user_config(chat_id, 'VOLUME_THRESHOLD', value)

            await update.message.reply_text(
                f"✅ <b>Минимальный объём установлен:</b> ${value:,}",
                reply_markup=user_keyboard,
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
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

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

            # Сохраняем настройки в зависимости от роли
            if user_manager.is_admin(chat_id):
                config_manager.set('SPREAD_THRESHOLD', value)
            else:
                user_manager.update_user_config(chat_id, 'SPREAD_THRESHOLD', value)

            await update.message.reply_text(
                f"✅ <b>Минимальный спред установлен:</b> {value}%",
                reply_markup=user_keyboard,
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
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

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

            # Сохраняем настройки в зависимости от роли
            if user_manager.is_admin(chat_id):
                config_manager.set('NATR_THRESHOLD', value)
            else:
                user_manager.update_user_config(chat_id, 'NATR_THRESHOLD', value)

            await update.message.reply_text(
                f"✅ <b>Минимальный NATR установлен:</b> {value}%",
                reply_markup=user_keyboard,
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

        # Добавляем обработчик callback запросов для инлайн кнопок
        self.app.add_handler(CallbackQueryHandler(self.callback_query_handler))

        return self.app

    async def callback_query_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик инлайн кнопок"""
        query = update.callback_query
        await query.answer()

        if not user_manager.is_admin(query.from_user.id):
            await query.edit_message_text("❌ У вас нет прав администратора")
            return

        data = query.data

        if data.startswith("approve_"):
            chat_id = data.replace("approve_", "")
            await self.admin_handlers.handle_approve_user(update, context, chat_id)
        elif data.startswith("reject_"):
            chat_id = data.replace("reject_", "")
            await self.admin_handlers.handle_reject_user(update, context, chat_id)
        elif data.startswith("revoke_"):
            chat_id = data.replace("revoke_", "")
            await self.admin_handlers.handle_revoke_user(update, context, chat_id)
        elif data == "show_all_users":
            await self.admin_handlers.handle_show_all_users(update, context)
        elif data.startswith("users_page_"):
            # Обработка пагинации пользователей (можно расширить позже)
            page = int(data.replace("users_page_", ""))
            await self.admin_handlers.handle_show_all_users(update, context)
        elif data == "add_more_coin":
            await self._handle_add_more_coin(update, context)
        elif data == "setup_filters":
            await self._handle_setup_filters_callback(update, context)

    async def _handle_initial_coin_input(self, update: Update, text: str):
        """Обработка ввода монеты во время первоначальной настройки (без кнопок)"""
        chat_id = update.effective_chat.id

        # Игнорируем команды и кнопки - ждем только название монеты
        if text.startswith('/') or text in ['🔔 Уведомления', '📊 Мониторинг', '➕ Добавить', '➖ Удалить', 
                                           '📋 Список', '⚙ Настройки', '📈 Активность 24ч', 'ℹ Статус', '🛑 Стоп']:
            await update.message.reply_text(
                "💡 <b>Сначала добавьте монету!</b>\n\n"
                "Введите название монеты для добавления в ваш список.\n\n"
                "Например: BTC, ETH, ADA, SOL",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END

        # Обрабатываем введенную монету
        symbol = text.upper().replace('_USDT', '').replace('USDT', '').strip()

        # Валидация символа
        if not input_validator.validate_symbol(symbol):
            await update.message.reply_text(
                "❌ <b>Неверный формат символа</b>\n\n"
                "Символ должен содержать только буквы и цифры (2-10 символов)\n\n"
                "💡 Попробуйте еще раз:\n"
                "Примеры: BTC, ETH, ADA, SOL",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END

        # Проверяем существование монеты
        try:
            loading_msg = await update.message.reply_text("🔍 Проверяю монету...")

            ticker_data = await api_client.get_ticker_data(symbol)

            if loading_msg:
                await loading_msg.delete()

            if not ticker_data:
                await update.message.reply_text(
                    f"❌ <b>Монета '{symbol}' не найдена на MEXC</b>\n\n"
                    "Попробуйте ввести другое название монеты.\n\n"
                    "Примеры: BTC, ETH, ADA, SOL",
                    parse_mode=ParseMode.HTML
                )
                return ConversationHandler.END

            # Проверяем, нет ли уже этой монеты в списке пользователя
            user_watchlist = user_manager.get_user_watchlist(chat_id)
            if symbol in user_watchlist:
                await update.message.reply_text(
                    f"⚠️ Монета <b>{symbol}</b> уже в вашем списке\n\n"
                    "Введите название другой монеты:",
                    parse_mode=ParseMode.HTML
                )
                return ConversationHandler.END

            # Добавляем монету
            if user_manager.add_user_coin(chat_id, symbol):
                user_watchlist = user_manager.get_user_watchlist(chat_id)
                price = float(ticker_data.get('lastPrice', 0))

                # ВАЖНО: Очищаем состояние setup, чтобы предотвратить повторную обработку
                user_manager.update_user_data(chat_id, {'setup_state': 'coin_added_waiting_choice'})

                # Создаем inline кнопки
                from telegram import InlineKeyboardMarkup, InlineKeyboardButton
                keyboard = [
                    [InlineKeyboardButton("➕ Добавить еще монету", callback_data="add_more_coin")],
                    [InlineKeyboardButton("⚙️ Перейти к настройкам", callback_data="setup_filters")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await update.message.reply_text(
                    f"✅ <b>Монета добавлена!</b>\n\n"
                    f"📊 <b>{symbol}</b>\n"
                    f"💰 Цена: <code>${price:.6f}</code>\n"
                    f"📈 Всего монет: <b>{len(user_watchlist)}</b>\n\n"
                    "🔄 <b>Что дальше?</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )
            else:
                await update.message.reply_text(
                    f"⚠️ Монета <b>{symbol}</b> уже в вашем списке\n\n"
                    "Введите название другой монеты:",
                    parse_mode=ParseMode.HTML
                )

        except Exception as e:
            if 'loading_msg' in locals():
                try:
                    await loading_msg.delete()
                except:
                    pass

            await update.message.reply_text(
                f"❌ Ошибка при проверке монеты {symbol}\n\n"
                "Попробуйте ввести другое название:",
                parse_mode=ParseMode.HTML
            )

        return ConversationHandler.END

    async def _handle_initial_filter_input(self, update: Update, text: str):
        """Обработка ввода фильтров во время первоначальной настройки"""
        chat_id = update.effective_chat.id
        user_data = user_manager.get_user_data(chat_id)
        setup_state = user_data.get('setup_state', '')

        # Если пользователь написал "далее" во время добавления монет
        if text.lower() in ['далее', 'готово', 'продолжить'] and user_data.get('setup_state') == 'initial_coin_setup':
            user_watchlist = user_manager.get_user_watchlist(chat_id)
            if user_watchlist:
                await self._start_filter_setup_initial(update, chat_id)
                return ConversationHandler.END
            else:
                await update.message.reply_text(
                    "❌ <b>Добавьте хотя бы одну монету</b>\n\n"
                    "Введите название монеты:",
                    parse_mode=ParseMode.HTML
                )
                return ConversationHandler.END

        # Обработка настройки фильтров
        if setup_state == 'setting_filters_volume':
            try:
                value = int(text)
                if value < 100:
                    await update.message.reply_text(
                        "❌ Объём должен быть не менее $100\n\n"
                        "Введите корректное значение:",
                        parse_mode=ParseMode.HTML
                    )
                    return ConversationHandler.END

                user_manager.update_user_config(chat_id, 'VOLUME_THRESHOLD', value)
                user_manager.update_user_data(chat_id, {'setup_state': 'setting_filters_spread'})

                await update.message.reply_text(
                    f"✅ <b>Объём установлен:</b> ${value:,}\n\n"
                    "📈 <b>2/3 - Минимальный спред</b>\n\n"
                    "Введите минимальный спред в процентах.\n\n"
                    "💡 <b>Рекомендуется:</b> 0.1-0.5\n"
                    "Спред - разница между ценой покупки и продажи\n\n"
                    "Введите число (например: 0.1):",
                    parse_mode=ParseMode.HTML
                )

            except ValueError:
                await update.message.reply_text(
                    "❌ Введите числовое значение\n\n"
                    "Например: 1000",
                    parse_mode=ParseMode.HTML
                )

        elif setup_state == 'setting_filters_spread':
            try:
                value = float(text)
                if value < 0 or value > 10:
                    await update.message.reply_text(
                        "❌ Спред должен быть от 0 до 10%\n\n"
                        "Введите корректное значение:",
                        parse_mode=ParseMode.HTML
                    )
                    return ConversationHandler.END

                user_manager.update_user_config(chat_id, 'SPREAD_THRESHOLD', value)
                user_manager.update_user_data(chat_id, {'setup_state': 'setting_filters_natr'})

                await update.message.reply_text(
                    f"✅ <b>Спред установлен:</b> {value}%\n\n"
                    "📊 <b>3/3 - Минимальный NATR</b>\n\n"
                    "Введите минимальный NATR в процентах.\n\n"
                    "💡 <b>Рекомендуется:</b> 0.5-2.0\n"
                    "NATR показывает волатильность монеты\n\n"
                    "Введите число (например: 0.5):",
                    parse_mode=ParseMode.HTML
                )

            except ValueError:
                await update.message.reply_text(
                    "❌ Введите числовое значение\n\n"
                    "Например: 0.1",
                    parse_mode=ParseMode.HTML
                )

        elif setup_state == 'setting_filters_natr':
            try:
                value = float(text)
                if value < 0 or value > 20:
                    await update.message.reply_text(
                        "❌ NATR должен быть от 0 до 20%\n\n"
                        "Введите корректное значение:",
                        parse_mode=ParseMode.HTML
                    )
                    return ConversationHandler.END

                user_manager.update_user_config(chat_id, 'NATR_THRESHOLD', value)
                user_manager.mark_setup_completed(chat_id)
                user_manager.update_user_data(chat_id, {'setup_state': 'completed'})

                user_config = user_manager.get_user_config(chat_id)
                user_watchlist = user_manager.get_user_watchlist(chat_id)

                await update.message.reply_text(
                    f"🎉 <b>Настройка завершена!</b>\n\n"
                    f"✅ <b>Ваши настройки сохранены:</b>\n"
                    f"• Объём: ${user_config.get('VOLUME_THRESHOLD'):,}\n"
                    f"• Спред: {user_config.get('SPREAD_THRESHOLD')}%\n"
                    f"• NATR: {user_config.get('NATR_THRESHOLD')}%\n\n"
                    f"📋 <b>Ваши монеты:</b> {len(user_watchlist)} шт.\n"
                    f"• {', '.join(user_watchlist[:5])}"
                    f"{'...' if len(user_watchlist) > 5 else ''}\n\n"
                    "🚀 <b>Теперь вы можете использовать бота!</b>\n\n"
                    "Используйте кнопки меню ниже:",
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.user_keyboard
                )

            except ValueError:
                await update.message.reply_text(
                    "❌ Введите числовое значение\n\n"
                    "Например: 0.5",
                    parse_mode=ParseMode.HTML
                )

        return ConversationHandler.END

    async def _start_filter_setup_initial(self, update: Update, chat_id: str):
        """Начинает первоначальную настройку фильтров без кнопок"""
        user_manager.update_user_data(chat_id, {'setup_state': 'setting_filters_volume'})

        await update.message.reply_text(
            "⚙️ <b>Настройка фильтров</b>\n\n"
            "Теперь настройте фильтры, чтобы бот уведомлял только об интересующих вас монетах.\n\n"
            "📊 <b>1/3 - Минимальный объём</b>\n\n"
            "Введите минимальный объём торгов в долларах.\n\n"
            "💡 <b>Рекомендуется:</b> 500-2000\n"
            "Объём - суммарный объём торгов за последние 24ч\n\n"
            "Введите число (например: 1000):",
            parse_mode=ParseMode.HTML
        )

    async def _handle_add_more_coin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопки 'Добавить еще монету'"""
        query = update.callback_query
        await query.answer()

        chat_id = query.from_user.id

        await query.edit_message_text(
            "➕ <b>Добавление монеты</b>\n\n"
            "Введите символ следующей монеты:\n\n"
            "Примеры: BTC, ETH, ADA, SOL",
            parse_mode=ParseMode.HTML
        )

        # Устанавливаем состояние для добавления следующей монеты
        user_manager.update_user_data(chat_id, {'setup_state': 'initial_coin_setup'})

    async def _handle_setup_filters_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопки 'Перейти к настройкам'"""
        query = update.callback_query
        await query.answer()

        chat_id = query.from_user.id

        await query.edit_message_text(
            "⚙️ <b>Переход к настройке фильтров</b>\n\n"
            "Отлично! Теперь настроим фильтры для поиска активных монет.",
            parse_mode=ParseMode.HTML
        )

        # Запускаем настройку фильтров
        await self._start_filter_setup_initial_callback(query, chat_id)

    async def _start_filter_setup_initial_callback(self, query, chat_id: str):
        """Начинает первоначальную настройку фильтров после callback"""
        user_manager.update_user_data(chat_id, {'setup_state': 'setting_filters_volume'})

        await query.message.reply_text(
            "📊 <b>1/3 - Минимальный объём</b>\n\n"
            "Введите минимальный объём торгов в долларах.\n\n"
            "💡 <b>Рекомендуется:</b> 500-2000\n"
            "Объём - суммарный объём торгов за последние 24ч\n\n"
            "Введите число (например: 1000):",
            parse_mode=ParseMode.HTML
        )

    

    async def initial_setup_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик для первоначальной настройки (добавление монет и фильтров)"""
        chat_id = update.effective_chat.id
        text = update.message.text

        user_data = user_manager.get_user_data(chat_id)
        setup_state = user_data.get('setup_state', '')

        if setup_state == 'initial_coin_setup':
            return await self._handle_initial_coin_input(update, text)
        elif setup_state.startswith('setting_filters'):
            return await self._handle_initial_filter_input(update, text)

        else:
            # Если состояние не определено, показываем стандартное приветствие
            await update.message.reply_text(
                "💡 <b>Пожалуйста, используйте кнопки меню</b>\n\n"
                "Выберите действие из доступных опций:",
                parse_mode=ParseMode.HTML,
                reply_markup=self.user_keyboard
            )
            return ConversationHandler.END


    async def approve_user(self, chat_id: str) -> bool:
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
            'config': {
                'VOLUME_THRESHOLD': 1000,
                'SPREAD_THRESHOLD': 0.1,
                'NATR_THRESHOLD': 0.5
            },
            'active_coins': {},
            'last_activity': time.time(),
            'setup_state': 'initial_coin_setup'  # Сразу устанавливаем состояние настройки
        }

        # Удаляем из заявок
        del self.pending_requests[chat_id_str]
        self.save_data()

        bot_logger.info(f"Пользователь {chat_id_str} одобрен")
        return True
# Modified bot to handle initial configuration and filter input, and new functions for manage the states of setup.
telegram_bot = TradingTelegramBot()