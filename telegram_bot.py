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
        self.last_message_time = 0
        self.message_cache = {}
        self._message_queue = asyncio.Queue()
        self._queue_processor_task = None

        # Защита от одновременных операций
        self._operation_lock = asyncio.Lock()
        self._switching_mode = False
        self._last_operation_time = 0

        # Активные монеты для уведомлений
        self._active_coins: Dict[str, Dict] = {}
        self.processing_coins = set()
        self.notification_locks = set()

        # ID сообщения мониторинга
        self.monitoring_message_id: Optional[int] = None

        # Многопользовательские модули
        self.admin_handlers = create_admin_handlers(self)
        self.user_session_recorders: Dict[str, UserSessionRecorder] = {}

        # Инициализируем менеджер режимов пользователей
        from user_modes_manager import UserModesManager
        self.user_modes_manager = UserModesManager(self)

        # Состояния ConversationHandler
        self.ADDING_COIN, self.REMOVING_COIN = range(2)

        self._setup_keyboards()

    @property
    def active_coins(self):
        """Свойство для обратной совместимости с основным health check"""
        return self._active_coins

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
        return user_manager.get_user_watchlist(chat_id)

    def get_user_config(self, chat_id: str) -> Dict:
        """Получает конфигурацию пользователя"""
        # Все пользователи (включая админа) используют персональные настройки
        return user_manager.get_user_config(chat_id)

    def _setup_keyboards(self):
        """Настраивает клавиатуры"""
        # Клавиатура администратора
        self.admin_keyboard = ReplyKeyboardMarkup([
            ["🚀 Запуск бота", "🛑 Остановка"],
            ["🔄 Обновить мониторинг"],
            ["➕ Добавить", "➖ Удалить"],
            ["📋 Список", "ℹ Статус"],
            ["📈 Активность 24ч"],
            ["👥 Список заявок", "📋 Логи"],
            ["👤 Управление пользователями", "🧹 Очистить пользователей"]
        ], resize_keyboard=True, one_time_keyboard=False)

        # Обычная клавиатура для одобренных пользователей
        user_keyboard = ReplyKeyboardMarkup([
            ['➕ Добавить', '➖ Удалить'],
            ['📋 Список']
        ], resize_keyboard=True)

        # Клавиатура обычного пользователя
        self.user_keyboard = ReplyKeyboardMarkup([
            ["🚀 Запуск бота", "🛑 Остановка"],
            ["🔄 Обновить мониторинг"],
            ["➕ Добавить", "➖ Удалить"],
            ["📋 Список", "ℹ Статус"],
            ["📈 Активность 24ч"]
        ], resize_keyboard=True, one_time_keyboard=False)

        # Основная клавиатура (используется по умолчанию для админа)
        self.main_keyboard = self.admin_keyboard

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

    async def _send_personal_message(self, chat_id: str, text: str, reply_markup=None) -> Optional[int]:
        """Отправляет персональное сообщение пользователю"""
        if not self.app or not self.app.bot:
            return None

        try:
            message = await self.app.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            return message.message_id if message else None
        except Exception as e:
            bot_logger.debug(f"Ошибка отправки персонального сообщения {chat_id}: {e}")
            return None

    async def _edit_personal_message(self, chat_id: str, message_id: int, text: str, reply_markup=None):
        """Редактирует персональное сообщение пользователя"""
        if not self.app or not self.app.bot:
            return

        try:
            await self.app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            bot_logger.debug(f"Ошибка редактирования персонального сообщения {chat_id}: {e}")

    async def _delete_personal_message(self, chat_id: str, message_id: int):
        """Удаляет персональное сообщение пользователя"""
        if not self.app or not self.app.bot:
            return

        try:
            await self.app.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            bot_logger.debug(f"Ошибка удаления персонального сообщения {chat_id}: {e}")

    async def _fetch_user_data(self, watchlist: List[str], chat_id: str):
        """Получает данные для режима пользователя"""
        results = []
        failed_coins = []

        # Получаем фильтры пользователя
        user_config = user_manager.get_user_config(chat_id)
        vol_threshold = user_config.get('VOLUME_THRESHOLD', 1000)
        spread_threshold = user_config.get('SPREAD_THRESHOLD', 0.1)
        natr_threshold = user_config.get('NATR_THRESHOLD', 0.5)

        batch_size = 10
        for batch in self._chunks(watchlist, batch_size):
            try:
                batch_data = await api_client.get_batch_coin_data(batch)
                for symbol, coin_data in batch_data.items():
                    if coin_data:
                        # Применяем фильтры пользователя
                        coin_data['active'] = (
                            coin_data.get('volume', 0) >= vol_threshold and
                            coin_data.get('spread', 0) >= spread_threshold and
                            coin_data.get('natr', 0) >= natr_threshold and
                            coin_data.get('trades', 0) > 0
                        )
                        results.append(coin_data)
                    else:
                        failed_coins.append(symbol)
            except Exception as e:
                bot_logger.warning(f"API запрос не удался для {chat_id}: {e}")
                failed_coins.extend(batch)

            await asyncio.sleep(0.5)  # Немного больше задержка

        return results, failed_coins

    def _chunks(self, lst: List, size: int):
        """Разбивает список на чанки"""
        for i in range(0, len(lst), size):
            yield lst[i:i + size]

    async def _stop_bot(self):
        """Останавливает бота"""
        async with self._operation_lock:
            if self._switching_mode:
                bot_logger.debug("Переключение режима уже в процессе, пропускаем")
                return

            if not self.bot_running:
                return

            self._switching_mode = True

            try:
                bot_logger.info(f"🛑 Останавливаем бота")

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

                # Удаляем все активные уведомления
                deleted_count = 0
                for symbol, coin_data in list(self._active_coins.items()):
                    msg_id = coin_data.get('msg_id')
                    if msg_id and isinstance(msg_id, int) and msg_id > 0:
                        await self.delete_message(msg_id)
                        deleted_count += 1

                if deleted_count > 0:
                    bot_logger.info(f"🗑 Удалено {deleted_count} уведомлений")

                # Удаляем сообщение мониторинга
                if self.monitoring_message_id:
                    await self.delete_message(self.monitoring_message_id)
                    bot_logger.info("📝 Сообщение мониторинга удалено")

                # Очищаем состояние
                self.bot_running = False
                self._active_coins.clear()
                self.processing_coins.clear()
                self.notification_locks.clear()
                self.monitoring_message_id = None
                self.message_cache.clear()

                # Даем время для завершения операций
                await asyncio.sleep(0.3)

                bot_logger.info("✅ Бот успешно остановлен")

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
        welcome_text = (
            "🤖 <b>Добро пожаловать, Администратор!</b>\n\n"
            "🚀 <b>Запуск бота</b> - мониторинг и уведомления\n"
            "🛑 <b>Остановка</b> - прекращение работы\n\n"
            "👥 <b>Администрирование:</b>\n"
            "• 👥 Список заявок - управление новыми пользователями\n"
            "• 📋 Логи - просмотр системных логов\n"
            "• 👤 Управление пользователями\n\n"
        )

        welcome_text += "Выберите действие:"
        await update.message.reply_text(welcome_text, reply_markup=self.admin_keyboard, parse_mode=ParseMode.HTML)

    async def _handle_approved_user_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка старта для одобренного пользователя"""
        chat_id = update.effective_chat.id
        user_watchlist = user_manager.get_user_watchlist(chat_id)

        # Всегда показываем пользователю меню с кнопками
        welcome_text = (
            "🤖 <b>Добро пожаловать в MEXCScalping Assistant!</b>\n\n"
            "🚀 <b>Запуск бота</b> - мониторинг и уведомления\n"
            "🛑 <b>Остановка</b> - прекращение работы\n\n"
            "⚙ <b>Управление:</b>\n"
            "• ➕ Добавить монету в ваш список\n"
            "• ➖ Удалить монету из списка\n"
            "• 📋 Показать ваши монеты\n\n"
        )

        # Если нет монет, добавляем напоминание
        if not user_watchlist:
            welcome_text += (
                "⚠️ <b>Важно:</b> У вас нет монет для отслеживания!\n"
                "Нажмите ➕ <b>Добавить</b> чтобы добавить первую монету.\n\n"
                "💡 <b>Совет:</b> После добавления монет настройте фильтры через ⚙ <b>Настройки</b>\n"
                "для более точного поиска активных возможностей.\n\n"
            )
        else:
            welcome_text += (
                f"📋 <b>Ваши монеты:</b> {len(user_watchlist)} шт.\n\n"
                "💡 <b>Подсказка:</b> Проверьте ⚙ <b>Настройки</b> для оптимизации фильтров поиска.\n\n"
            )

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

        # Защита от spam нажатий (минимум 1 секунда между операциями)
        if current_time - self._last_operation_time < 1.0:
            bot_logger.debug("Слишком быстрые нажатия, игнорируем")
            return ConversationHandler.END

        self._last_operation_time = current_time
        user_keyboard = self.get_user_keyboard(chat_id)

        try:
            # Проверяем, не идет ли уже переключение режима
            if self._switching_mode:
                await update.message.reply_text(
                    "⏳ Идет переключение режима, подождите...",
                    reply_markup=user_keyboard
                )
                return ConversationHandler.END

            message_text = update.message.text

            # Админские функции - только для администратора
            if message_text == "👥 Список заявок":
                if user_manager.is_admin(chat_id):
                    await self.admin_handlers.handle_pending_requests(update, context)
                else:
                    await update.message.reply_text(
                        "❌ У вас нет прав для выполнения этого действия",
                        reply_markup=user_keyboard
                    )
            elif message_text == "📋 Логи":
                if user_manager.is_admin(chat_id):
                    await self.admin_handlers.handle_logs_request(update, context)
                else:
                    await update.message.reply_text(
                        "❌ У вас нет прав для выполнения этого действия",
                        reply_markup=user_keyboard
                    )
            elif message_text == "👤 Управление пользователями":
                if user_manager.is_admin(chat_id):
                    await self.admin_handlers.handle_user_management(update, context)
                else:
                    await update.message.reply_text(
                        "❌ У вас нет прав для выполнения этого действия",
                        reply_markup=user_keyboard
                    )
            elif message_text == "🧹 Очистить пользователей":
                if user_manager.is_admin(chat_id):
                    await self.admin_handlers.handle_clear_all_users(update, context)
                else:
                    await update.message.reply_text(
                        "❌ У вас нет прав для выполнения этого действия",
                        reply_markup=user_keyboard
                    )

            # Общие кнопки для всех пользователей
            elif text == "🚀 Запуск бота":
                await self._handle_start_bot(update)
            elif text == "🛑 Остановка":
                await self._handle_stop_bot(update)
            elif text == "➕ Добавить":
                return await self._handle_add_coin_start(update)
            elif text == "➖ Удалить":
                return await self._handle_remove_coin_start(update)
            elif text == "📋 Список":
                await self._handle_show_list(update)
            elif text == "ℹ Статус":
                await self._handle_status(update)
            elif text == "🔄 Обновить мониторинг":
                await self._handle_refresh_monitoring(update)
            elif text == "📈 Активность 24ч":
                bot_logger.info(f"📈 Обработка кнопки 'Активность 24ч' для пользователя {chat_id} {'(админ)' if user_manager.is_admin(chat_id) else '(пользователь)'}")
                await self._handle_activity_24h(update)
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

    async def _handle_activity_24h(self, update: Update, context: ContextTypes.DEFAULT_TYPE = None):
        """Обрабатывает запрос активности за 24 часа"""
        try:
            chat_id = update.effective_chat.id
            bot_logger.info(f"📈 Обработка кнопки 'Активность 24ч' для пользователя {chat_id}")

            # Получаем отчет активности пользователя
            from user_activity_calculator import user_activity_manager

            report = user_activity_manager.get_user_activity_report(chat_id)

            user_keyboard = self.get_user_keyboard(chat_id)

            await update.message.reply_text(
                report,
                parse_mode="HTML",
                reply_markup=user_keyboard
            )

        except Exception as e:
            bot_logger.error(f"Ошибка получения активности 24ч: {e}")
            user_keyboard = self.get_user_keyboard(update.effective_chat.id)
            await update.message.reply_text(
                "❌ Ошибка получения статистики активности",
                reply_markup=user_keyboard
            )

    async def _handle_start_bot(self, update: Update):
        """Обработка запуска бота"""
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

        # Проверяем права доступа
        if not user_manager.is_admin(chat_id) and not user_manager.is_user_approved(chat_id):
            await update.message.reply_text(
                "❌ У вас нет доступа к боту.",
                reply_markup=user_keyboard,
                parse_mode="HTML"
            )
            return

        # Проверяем список монет пользователя
        user_watchlist = user_manager.get_user_watchlist(chat_id)
        if not user_watchlist:
            await update.message.reply_text(
                "⚠️ <b>Ваш список отслеживания пуст!</b>\n\n"
                "Добавьте монеты в свой список нажав ➕ <b>Добавить</b>",
                reply_markup=user_keyboard,
                parse_mode="HTML"
            )
            return

        if self.user_modes_manager.is_user_mode_running(chat_id):
            await update.message.reply_text(
                "✅ Ваш бот уже запущен.",
                reply_markup=user_keyboard
            )
            return

        # Запускаем режим для пользователя
        success = await self.user_modes_manager.start_user_mode(chat_id)

        if success:
            await update.message.reply_text(
                f"✅ <b>Ваш бот запущен</b>\n"
                f"🔄 Мониторинг активен для {len(user_watchlist)} ваших монет",
                reply_markup=user_keyboard,
                parse_mode="HTML"
            )
            bot_logger.info(f"Пользователь {chat_id} запустил свой режим")
        else:
            await update.message.reply_text(
                "❌ Не удалось запустить режим. Попробуйте позже.",
                reply_markup=user_keyboard,
                parse_mode="HTML"
            )

    async def _handle_stop_bot(self, update: Update):
        """Обработка остановки бота"""
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

        # Проверяем права доступа
        if not user_manager.is_admin(chat_id) and not user_manager.is_user_approved(chat_id):
            await update.message.reply_text(
                "❌ У вас нет доступа к боту.",
                reply_markup=user_keyboard,
                parse_mode="HTML"
            )
            return

        if not self.user_modes_manager.is_user_mode_running(chat_id):
            await update.message.reply_text(
                "ℹ️ Ваш бот уже остановлен.",
                reply_markup=user_keyboard
            )
            return

        # Останавливаем режим пользователя
        success = await self.user_modes_manager.stop_user_mode(chat_id)

        if success:
            await update.message.reply_text(
                "🛑 <b>Ваш бот остановлен</b>",
                reply_markup=user_keyboard,
                parse_mode=ParseMode.HTML
            )
            bot_logger.info(f"Пользователь {chat_id} остановил свой режим")
        else:
            await update.message.reply_text(
                "❌ Не удалось остановить режим.",
                reply_markup=user_keyboard,
                parse_mode=ParseMode.HTML
            )

    async def _start_bot_mode(self):
        """Запуск бота с мониторингом и уведомлениями"""
        if self.bot_running:
            bot_logger.warning("Бот уже запущен")
            return

        self.bot_running = True
        self._active_coins.clear()
        self.processing_coins.clear()
        self.notification_locks.clear()
        self.monitoring_message_id = None

        bot_logger.info("🚀 Запуск MEXCScalping Assistant")

        # Запускаем процессор очереди
        await self._start_message_queue_processor()

        # Отправляем начальное сообщение мониторинга
        initial_text = "🔄 <b>Инициализация мониторинга...</b>"
        self.monitoring_message_id = await self.send_message(initial_text)

        # Запускаем основной цикл
        self.task = asyncio.create_task(self._main_loop())

    async def _main_loop(self):
        """Основной цикл работы бота"""
        cycle_count = 0
        cleanup_counter = 0

        while self.bot_running:
            try:
                cycle_count += 1

                # Проверяем список отслеживания
                watchlist = watchlist_manager.get_all()
                if not watchlist:
                    no_coins_text = "❌ <b>Список отслеживания пуст</b>\nДобавьте монеты для мониторинга."
                    if self.monitoring_message_id:
                        await self.edit_message(self.monitoring_message_id, no_coins_text)
                    await asyncio.sleep(config_manager.get('MONITORING_UPDATE_INTERVAL'))
                    continue

                # Периодическая очистка
                cleanup_counter += 1
                if cleanup_counter >= 10:
                    await self._cleanup_stale_processes()
                    # Проверяем неактивные сессии
                    try:
                        from session_recorder import session_recorder
                        session_recorder.check_inactive_sessions(self._active_coins)
                    except Exception as e:
                        bot_logger.debug(f"Ошибка проверки сессий: {e}")
                    cleanup_counter = 0

                # Получаем данные монет (активность уже рассчитана внутри)
                results, failed_coins = await self._fetch_bot_data()

                # Обрабатываем каждую монету для уведомлений
                for coin_data in results:
                    if not self.bot_running:
                        break

                    symbol = coin_data['symbol']

                    # Защита от одновременной обработки
                    if symbol in self.processing_coins:
                        continue

                    try:
                        self.processing_coins.add(symbol)
                        await self._process_coin_notification(symbol, coin_data)
                    except Exception as e:
                        bot_logger.error(f"Ошибка обработки {symbol}: {e}")
                    finally:
                        self.processing_coins.discard(symbol)

                # Записываем данные активных монет в сессии
                for coin_data in results:
                    if coin_data.get('active'):
                        try:
                            from session_recorder import session_recorder
                            session_recorder.update_coin_activity(coin_data['symbol'], coin_data)
                        except Exception as e:
                            bot_logger.debug(f"Ошибка записи сессии {coin_data['symbol']}: {e}")

                # Обновляем отчет мониторинга
                if results:
                    report = self._format_monitoring_report(results, failed_coins)
                    if self.monitoring_message_id:
                        await self.edit_message(self.monitoring_message_id, report)
                    else:
                        self.monitoring_message_id = await self.send_message(report)

                # Периодическая очистка
                if cycle_count % 50 == 0:
                    import gc
                    gc.collect()
                    try:
                        from cache_manager import cache_manager
                        cache_manager.clear_expired()
                    except:
                        pass

                await asyncio.sleep(config_manager.get('MONITORING_UPDATE_INTERVAL'))

            except asyncio.CancelledError:
                break
            except Exception as e:
                bot_logger.error(f"Ошибка в основном цикле: {e}")
                await asyncio.sleep(1.0)

    async def _fetch_bot_data(self):
        """Получает данные для работы бота"""
        watchlist = list(watchlist_manager.get_all())
        results = []
        failed_coins = []

        # Получаем фильтры из настроек администратора
        admin_config = user_manager.get_user_config(user_manager.admin_chat_id)
        vol_threshold = admin_config.get('VOLUME_THRESHOLD', 1000)
        spread_threshold = admin_config.get('SPREAD_THRESHOLD', 0.1)
        natr_threshold = admin_config.get('NATR_THRESHOLD', 0.5)

        batch_size = config_manager.get('CHECK_BATCH_SIZE', 15)
        for batch in self._chunks(watchlist, batch_size):
            if not self.bot_running:
                break

            try:
                batch_data = await api_client.get_batch_coin_data(batch)
                for symbol, coin_data in batch_data.items():
                    if coin_data:
                        # Применяем правильные фильтры для определения активности
                        coin_data['active'] = (
                            coin_data.get('volume', 0) >= vol_threshold and
                            coin_data.get('spread', 0) >= spread_threshold and
                            coin_data.get('natr', 0) >= natr_threshold and
                            coin_data.get('trades', 0) > 0
                        )
                        results.append(coin_data)
                    else:
                        # Пробуем получить из кеша при ошибке API
                        try:
                            from cache_manager import cache_manager
                            cached_data = cache_manager.get_ticker_cache(symbol)
                            if cached_data:
                                # Создаем упрощенные данные из кеша
                                simplified_data = {
                                    'symbol': symbol,
                                    'price': float(cached_data.get('lastPrice', 0)),
                                    'volume': 0,  # Не знаем актуальный объём
                                    'change': 0,  # Не знаем актуальное изменение
                                    'spread': 0,
                                    'natr': 0,
                                    'trades': 0,
                                    'active': False,  # Помечаем как неактивную
                                    'has_recent_trades': False,
                                    'timestamp': time.time(),
                                    'from_cache': True  # Флаг что данные из кеша
                                }
                                results.append(simplified_data)
                            else:
                                failed_coins.append(symbol)
                        except:
                            failed_coins.append(symbol)
            except Exception as e:
                bot_logger.warning(f"API временно недоступен для batch {batch}: {e}")
                # При полной недоступности API пытаемся использовать кеш
                for symbol in batch:
                    try:
                        from cache_manager import cache_manager
                        cached_data = cache_manager.get_ticker_cache(symbol)
                        if cached_data:
                            simplified_data = {
                                'symbol': symbol,
                                'price': float(cached_data.get('lastPrice', 0)),
                                'volume': 0,
                                'change': 0,
                                'spread': 0,
                                'natr': 0,
                                'trades': 0,
                                'active': False,
                                'has_recent_trades': False,
                                'timestamp': time.time(),
                                'from_cache': True
                            }
                            results.append(simplified_data)
                        else:
                            failed_coins.append(symbol)
                    except:
                        failed_coins.append(symbol)

            await asyncio.sleep(config_manager.get('CHECK_BATCH_INTERVAL', 0.4))

        return results, failed_coins

    async def _process_coin_notification(self, symbol: str, data: Dict):
        """Обработка уведомлений монет"""
        now = time.time()

        # Записываем данные активных монет в сессии
        if data.get('active'):
            try:
                from session_recorder import session_recorder
                session_recorder.update_coin_activity(symbol, data)
                bot_logger.debug(f"📊 Данные {symbol} переданы в Session Recorder")
            except Exception as e:
                bot_logger.debug(f"Ошибка записи сессии {symbol}: {e}")

        # Проверяем алерты
        try:
            from advanced_alerts import advanced_alert_manager
            advanced_alert_manager.check_coin_alerts(symbol, data)
        except:
            pass

        if data['active']:
            # Монета активна
            if symbol not in self._active_coins:
                # Дополнительная защита от дублирования
                if symbol in self.notification_locks:
                    return

                self.notification_locks.add(symbol)
                try:
                    await self._create_coin_notification(symbol, data, now)
                finally:
                    self.notification_locks.discard(symbol)
            else:
                # Обновляем существующую монету
                await self._update_coin_notification(symbol, data, now)
        else:
            # Монета неактивна - проверяем завершение
            if symbol in self._active_coins:
                coin_info = self._active_coins[symbol]

                # Пропускаем если создается
                if coin_info.get('creating', False):
                    return

                inactivity_timeout = config_manager.get('INACTIVITY_TIMEOUT')
                if now - coin_info['last_active'] > inactivity_timeout:
                    await self._end_coin_activity(symbol, now)

    async def _create_coin_notification(self, symbol: str, data: Dict, now: float):
        """Создает новое уведомление для монеты"""
        if not self.bot_running:
            return

        bot_logger.info(f"[NOTIFICATION_START] {symbol} - новая активная монета обнаружена")

        # Создаем запись с флагом creating
        self._active_coins[symbol] = {
            'start': now,
            'last_active': now,
            'data': data.copy(),
            'creating': True,
            'creation_start': now
        }

        # Создаем сообщение
        message = (
            f"🚨 <b>{symbol}_USDT активен</b>\n"
            f"🔄 Изм: {data['change']:+.2f}%  🔁 Сделок: {data['trades']}\n"
            f"📊 Объём: ${data['volume']:,.2f}  NATR: {data['natr']:.2f}%\n"
            f"⇄ Спред: {data['spread']:.2f}%"
        )

        # Отправляем сообщение
        msg_id = await self.send_message(message)

        if msg_id and symbol in self._active_coins:
            # Обновляем запись с полученным msg_id
            self._active_coins[symbol].update({
                'msg_id': msg_id,
                'creating': False
            })
            bot_logger.trade_activity(symbol, "STARTED", f"Volume: ${data['volume']:,.2f}")
            bot_logger.info(f"[NOTIFICATION_SUCCESS] {symbol} - уведомление создано успешно")
        else:
            # Удаляем неудачную запись
            if symbol in self._active_coins:
                del self._active_coins[symbol]
            bot_logger.warning(f"[NOTIFICATION_FAILED] {symbol} - не удалось создать уведомление")

    async def _update_coin_notification(self, symbol: str, data: Dict, now: float):
        """Обновляет существующее уведомление"""
        if not self.bot_running:
            return

        coin_info = self._active_coins[symbol]

        # Пропускаем если создается
        if coin_info.get('creating', False):
            return

        # Обновляем данные
        coin_info['last_active'] = now
        coin_info['data'] = data

        # Обновляем сообщение если есть msg_id
        msg_id = coin_info.get('msg_id')
        if msg_id and isinstance(msg_id, int):
            new_message = (
                f"🚨 <b>{symbol}_USDT активен</b>\n"
                f"🔄 Изм: {data['change']:+.2f}%  🔁 Сделок: {data['trades']}\n"
                f"📊 Объём: ${data['volume']:,.2f}  NATR: {data['natr']:.2f}%\n"
                f"⇄ Спред: {data['spread']:.2f}%"
            )

            await self.edit_message(msg_id, new_message)

    async def _end_coin_activity(self, symbol: str, end_time: float):
        """Завершает активность монеты"""
        if symbol not in self._active_coins:
            return

        coin_info = self._active_coins[symbol]
        duration = end_time - coin_info['start']

        bot_logger.info(f"[END] Завершение активности {symbol}, длительность: {duration:.1f}с")

        # Удаляем сообщение об активности
        msg_id = coin_info.get('msg_id')
        if msg_id and isinstance(msg_id, int) and msg_id > 0:
            await self.delete_message(msg_id)

        # Отправляем сообщение о завершении если активность была >= 60 секунд
        if duration >= 60:
            duration_min = int(duration // 60)
            duration_sec = int(duration % 60)
            end_message = (
                f"✅ <b>{symbol}_USDT завершил активность</b>\n"
                f"⏱ Длительность: {duration_min} мин {duration_sec} сек"
            )
            await self.send_message(end_message)
            bot_logger.trade_activity(symbol, "ENDED", f"Duration: {duration_min}m {duration_sec}s")

        # Удаляем из активных монет
        del self._active_coins[symbol]

    async def _cleanup_stale_processes(self):
        """Очистка зависших процессов"""
        current_time = time.time()
        to_remove = []

        for symbol, coin_info in list(self._active_coins.items()):
            # Монеты без msg_id (orphaned)
            if not coin_info.get('msg_id') and not coin_info.get('creating', False):
                to_remove.append(symbol)
            # Зависшие процессы создания (больше 10 секунд)
            elif coin_info.get('creating', False):
                start_time = coin_info.get('creation_start', current_time)
                if current_time - start_time > 10:
                    to_remove.append(symbol)

        for symbol in to_remove:
            try:
                del self._active_coins[symbol]
                bot_logger.info(f"[CLEANUP] Очищена зависшая монета {symbol}")
            except Exception as e:
                bot_logger.error(f"[CLEANUP] Ошибка очистки {symbol}: {e}")

        # Очистка старых блокировок
        self.processing_coins.clear()

    def _format_monitoring_report(self, results: List[Dict], failed_coins: List[str]) -> str:
        """Форматирует отчет мониторинга"""
        results.sort(key=lambda x: x['volume'], reverse=True)

        parts = ["<b>📊 Скальпинг мониторинг (1м данные)</b>\n"]

        # Для админа показываем глобальные настройки, для обычных пользователей - их личные
        if user_manager.is_admin(self.chat_id):
            vol_thresh = 1000
            spread_thresh = 0.1
            natr_thresh = 0.5
            filter_prefix = "Глобальные фильтры"
        else:
            # Получаем конфигурацию администратора (основного пользователя)
            admin_config = user_manager.get_user_config(user_manager.admin_chat_id)
            vol_thresh = admin_config.get('VOLUME_THRESHOLD', 1000)
            spread_thresh = admin_config.get('SPREAD_THRESHOLD', 0.1)
            natr_thresh = admin_config.get('NATR_THRESHOLD', 0.5)
            filter_prefix = "Фильтры"

        parts.append(
            f"<i>{filter_prefix}: 1м оборот ≥${vol_thresh:,}, "
            f"Спред ≥{spread_thresh}%, NATR ≥{natr_thresh}%</i>\n"
        )

        if failed_coins:
            parts.append(f"⚠ <i>Ошибки: {', '.join(failed_coins[:5])}</i>\n")

        active_coins = [r for r in results if r['active']]
        if active_coins:
            parts.append("<b>🟢 АКТИВНЫЕ:</b>")
            for coin in active_coins[:10]:
                trades_icon = "🔥" if coin.get('has_recent_trades') else "📊"
                cache_icon = "💾" if coin.get('from_cache') else ""
                parts.append(
                    f"• <b>{coin['symbol']}</b>{cache_icon} "
                    f"${coin['volume']:,.0f} | {coin['change']:+.1f}% | "
                    f"{trades_icon}T:{coin['trades']} | S:{coin['spread']:.2f}% | N:{coin['natr']:.2f}%"
                )
            parts.append("")

        inactive_coins = [r for r in results if not r['active']]
        if inactive_coins:
            parts.append("<b>🔴 НЕАКТИВНЫЕ (топ по объёму):</b>")
            for coin in inactive_coins[:8]:
                trades_status = "✅" if coin['trades'] > 0 else "❌"
                cache_icon = "💾" if coin.get('from_cache') else ""
                parts.append(
                    f"• <b>{coin['symbol']}</b>{cache_icon} "
                    f"${coin['volume']:,.0f} | {coin['change']:+.1f}% | "
                    f"{trades_status}T:{coin['trades']} | S:{coin['spread']:.2f}% | N:{coin['natr']:.2f}%"
                )

        parts.append(f"\n📈 Активных: {len(active_coins)}/{len(results)}")

        report = "\n".join(parts)
        if len(report) > 4000:
            report = report[:4000] + "\n... <i>(отчет обрезан)</i>"

        return report

    # Остальные методы для работы с интерфейсом остаются без изменений...
    # [Здесь должны быть все остальные методы из оригинального класса]

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

        # Получаем список монет пользователя
        coins = user_manager.get_user_watchlist(chat_id)
        list_title = "ваш список"

        if len(coins) == 0:
            await update.message.reply_text(
                f"❌ {list_title.capitalize()} отслеживания пуст.",
                reply_markup=user_keyboard
            )
            return ConversationHandler.END

        # Показываем все монеты без обрезания
        coins_list = ", ".join(sorted(coins))

        await update.message.reply_text(
            f"➖ <b>Удаление монеты</b>\n\n"
            f"Текущий {list_title}: {coins_list}\n\n"
            f"Введите символ монеты для удаления:",
            reply_markup=self.back_keyboard,
            parse_mode=ParseMode.HTML
        )
        return self.REMOVING_COIN

    async def setup_application(self):
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

    async def add_coin_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик добавления монеты"""
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)
        text = update.message.text

        if text == "🔙 Назад":
            await self._handle_back(update)
            return ConversationHandler.END

        # Валидация символа
        if not input_validator.validate_symbol(text):
            await update.message.reply_text(
                "❌ Неверный формат символа. Используйте буквы и цифры (например: BTC или ETH)",
                reply_markup=self.back_keyboard,
                parse_mode=ParseMode.HTML
            )
            return self.ADDING_COIN

        symbol = text.upper().replace('_USDT', '').replace('USDT', '')

        try:
            # Проверяем существование монеты через API
            ticker_data = await api_client.get_ticker_data(symbol)
            if not ticker_data:
                await update.message.reply_text(
                    f"❌ Монета {symbol} не найдена на бирже MEXC",
                    reply_markup=self.back_keyboard,
                    parse_mode=ParseMode.HTML
                )
                return self.ADDING_COIN

            # Добавляем монету в список пользователя
            if user_manager.add_coin_to_user_watchlist(chat_id, symbol):
                await update.message.reply_text(
                    f"✅ Монета {symbol} добавлена в ваш список",
                    reply_markup=user_keyboard,
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    f"ℹ️ Монета {symbol} уже в вашем списке",
                    reply_markup=user_keyboard,
                    parse_mode=ParseMode.HTML
                )

        except Exception as e:
            bot_logger.error(f"Ошибка при добавлении монеты {symbol}: {e}")
            await update.message.reply_text(
                "❌ Ошибка при добавлении монеты. Попробуйте позже.",
                reply_markup=user_keyboard,
                parse_mode=ParseMode.HTML
            )

        return ConversationHandler.END

    async def remove_coin_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик удаления монеты"""
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)
        text = update.message.text

        if text == "🔙 Назад":
            await self._handle_back(update)
            return ConversationHandler.END

        symbol = text.upper().replace('_USDT', '').replace('USDT', '')

        # Удаляем монету из списка пользователя
        if user_manager.remove_coin_from_user_watchlist(chat_id, symbol):
            await update.message.reply_text(
                f"✅ Монета {symbol} удалена из вашего списка",
                reply_markup=user_keyboard,
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                f"❌ Монета {symbol} не найдена в вашем списке",
                reply_markup=user_keyboard,
                parse_mode=ParseMode.HTML
            )

        return ConversationHandler.END

    async def _handle_show_list(self, update: Update):
        """Показывает список монет"""
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

        # Получаем список монет пользователя
        coins = user_manager.get_user_watchlist(chat_id)
        list_title = "📋 Ваш список отслеживания"

        if not coins:
            await update.message.reply_text(
                f"📋 {list_title} пуст",
                reply_markup=user_keyboard,
                parse_mode=ParseMode.HTML
            )
            return

        coins.sort()
        coins_text = "\n".join([f"• {coin}" for coin in coins])

        message = f"📋 <b>{list_title}</b> ({len(coins)} монет):\n\n{coins_text}"

        if len(message) > 4000:
            message = message[:4000] + "\n... <i>(список обрезан)</i>"

        await update.message.reply_text(
            message,
            reply_markup=user_keyboard,
            parse_mode=ParseMode.HTML
        )

    async def _handle_status(self, update: Update):
        """Показывает статус бота"""
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

        user_running = self.user_modes_manager.is_user_mode_running(chat_id)
        user_status = "🟢 Работает" if user_running else "🔴 Остановлен"
        
        user_watchlist = user_manager.get_user_watchlist(chat_id)
        user_config = user_manager.get_user_config(chat_id)
        
        vol_thresh = user_config.get('VOLUME_THRESHOLD', 1000)
        spread_thresh = user_config.get('SPREAD_THRESHOLD', 0.1)
        natr_thresh = user_config.get('NATR_THRESHOLD', 0.5)
        
        # Получаем статистику режима пользователя
        user_stats = self.user_modes_manager.get_user_mode_stats(chat_id)
        active_coins_count = user_stats.get('active_coins', 0)
        
        message = (
            f"ℹ <b>Ваш статус</b>\n\n"
            f"🤖 Ваш бот: <code>{user_status}</code>\n"
            f"📋 Ваш список: {len(user_watchlist)} монет\n"
            f"📊 Ваших активных монет: <code>{active_coins_count}</code>\n"
            f"🎯 Ваши фильтры: 1м оборот ≥${vol_thresh:,}, Спред ≥{spread_thresh}%, NATR ≥{natr_thresh}%\n"
        )
        
        if user_running:
            uptime = user_stats.get('uptime', 0)
            uptime_str = f"{int(uptime//3600)}ч {int((uptime%3600)//60)}м" if uptime > 0 else "< 1м"
            message += f"⏱ Время работы: {uptime_str}\n"
            
        # Для админа показываем дополнительную статистику
        if user_manager.is_admin(chat_id):
            all_stats = self.user_modes_manager.get_all_stats()
            message += (
                f"\n👥 <b>Системная статистика (админ):</b>\n"
                f"• Всего пользователей: {all_stats['total_users']}\n"
                f"• Активных режимов: {all_stats['running_modes']}\n"
            )
            
        message += f"⏰ Последнее обновление: <code>{time.strftime('%H:%M:%S')}</code>"

        await update.message.reply_text(
            message,
            reply_markup=user_keyboard,
            parse_mode=ParseMode.HTML
        )

    async def _handle_refresh_monitoring(self, update: Update):
        """Обработчик обновления мониторинга"""
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

        if not self.user_modes_manager.is_user_mode_running(chat_id):
            await update.message.reply_text(
                "ℹ️ Ваш мониторинг не запущен.\nДля начала мониторинга нажмите 🚀 Запуск бота",
                reply_markup=user_keyboard,
                parse_mode=ParseMode.HTML
            )
            return

        await update.message.reply_text(
            "✅ <b>Запрос на обновление отправлен</b>\nВаш мониторинг обновится в следующем цикле",
            reply_markup=user_keyboard,
            parse_mode=ParseMode.HTML
        )



    async def _handle_back(self, update: Update):
        """Обработчик кнопки Назад"""
        chat_id = update.effective_chat.id
        user_keyboard = self.get_user_keyboard(chat_id)

        await update.message.reply_text(
            "🔙 Возврат в главное меню",
            reply_markup=user_keyboard,
            parse_mode=ParseMode.HTML
        )

    async def callback_query_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик инлайн кнопок"""
        query = update.callback_query
        await query.answer()

        chat_id = query.from_user.id
        data = query.data

        # Проверяем админские действия
        admin_actions = ["approve_", "reject_", "revoke_", "show_all_users", "users_page_"]
        is_admin_action = any(data.startswith(action) for action in admin_actions)

        if is_admin_action and not user_manager.is_admin(chat_id):
            await query.edit_message_text("❌ У вас нет прав администратора")
            return

        # Обрабатываем админские действия
        if data.startswith("approve_"):
            target_chat_id = data.replace("approve_", "")
            await self.admin_handlers.handle_approve_user(update, context, target_chat_id)
        elif data.startswith("reject_"):
            target_chat_id = data.replace("reject_", "")
            await self.admin_handlers.handle_reject_user(update, context, target_chat_id)
        elif data.startswith("revoke_"):
            target_chat_id = data.replace("revoke_", "")
            await self.admin_handlers.handle_revoke_user(update, context, target_chat_id)
        elif data == "show_all_users":
            await self.admin_handlers.handle_show_all_users(update, context)
        elif data == "activity_24h":
            # Импортируем здесь, чтобы избежать циклических импортов
            from user_activity_calculator import user_activity_manager

            activity_report = user_activity_manager.get_user_detailed_activity_report(str(chat_id))

            await query.edit_message_text(
                text=activity_report,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ Назад в личный кабинет", callback_data="personal_cabinet")]
                ]),
                parse_mode=ParseMode.HTML
            )

# Creates an instance of the bot
telegram_bot = TradingTelegramBot()