import asyncio
import time
import threading
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
        self.message_cache = {}
        self._notification_locks = set()
        self._processing_coins = set()  # Защита от одновременной обработки
        self._message_queue = asyncio.Queue()  # Очередь сообщений
        self._queue_processor_task = None

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
            return False

    def _chunks(self, lst: List, size: int):
        """Разбивает список на чанки"""
        for i in range(0, len(lst), size):
            yield lst[i:i + size]

    async def _stop_current_mode(self):
        """Останавливает текущий режим работы бота"""
        if self.bot_mode:
            bot_logger.info(f"🛑 Остановка режима: {self.bot_mode}")

            # Останавливаем циклы
            self.bot_running = False
            await asyncio.sleep(1.0)

            # Останавливаем процессор очереди
            if self._queue_processor_task and not self._queue_processor_task.done():
                self._queue_processor_task.cancel()
                try:
                    await asyncio.wait_for(self._queue_processor_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                self._queue_processor_task = None

            # Безопасная очистка очереди
            if self._message_queue is not None:
                try:
                    while not self._message_queue.empty():
                        try:
                            self._message_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        except RuntimeError:
                            # Очередь привязана к другому event loop
                            break
                except Exception as e:
                    bot_logger.debug(f"Ошибка очистки очереди: {e}")
                
                # Пересоздаем очередь для следующего использования
                try:
                    self._message_queue = asyncio.Queue()
                except Exception as e:
                    bot_logger.debug(f"Ошибка пересоздания очереди: {e}")
                    self._message_queue = None

            # Очищаем активные уведомления
            deleted_count = 0
            for symbol, coin_data in list(self.active_coins.items()):
                msg_id = coin_data.get('msg_id')
                if msg_id and isinstance(msg_id, int) and msg_id > 0:
                    try:
                        await self._direct_telegram_delete(msg_id)
                        deleted_count += 1
                    except Exception as e:
                        bot_logger.debug(f"Ошибка удаления {symbol}: {e}")

            if deleted_count > 0:
                bot_logger.info(f"🗑 Удалено {deleted_count} уведомлений")

            # Удаляем сообщение мониторинга
            if self.monitoring_message_id:
                try:
                    await self._direct_telegram_delete(self.monitoring_message_id)
                    bot_logger.info("📝 Сообщение мониторинга удалено")
                except Exception as e:
                    bot_logger.debug(f"Ошибка удаления мониторинга: {e}")
                self.monitoring_message_id = None

            # Полная очистка состояния
            self._force_clear_state()

            # Закрываем API сессию
            try:
                await api_client.close()
                await asyncio.sleep(0.2)
            except Exception as e:
                bot_logger.debug(f"Ошибка закрытия API: {e}")

    def _force_clear_state(self):
        """Принудительная очистка всех состояний"""
        self.monitoring_message_id = None
        self.active_coins.clear()
        self.message_cache.clear()
        self.bot_mode = None
        self._notification_locks.clear()
        self._processing_coins.clear()

        bot_state_manager.set_last_mode(None)
        bot_logger.debug("🧹 Принудительная очистка состояния выполнена")

    async def _notification_mode_loop(self):
        """Цикл режима уведомлений с защитой от дублирования"""
        bot_logger.info("Запущен режим уведомлений")

        # Запускаем процессор очереди сообщений
        await self._start_message_queue_processor()

        cleanup_counter = 0

        while self.bot_running and self.bot_mode == 'notification':
            watchlist = watchlist_manager.get_all()
            if not watchlist:
                await asyncio.sleep(config_manager.get('CHECK_FULL_CYCLE_INTERVAL'))
                continue

            # Периодическая очистка
            cleanup_counter += 1
            if cleanup_counter >= 10:
                await self._cleanup_stale_processes()
                cleanup_counter = 0

            batch_size = config_manager.get('CHECK_BATCH_SIZE')
            for batch in self._chunks(list(watchlist), batch_size):
                if not self.bot_running or self.bot_mode != 'notification':
                    break

                # Получаем данные батча
                batch_data = await api_client.get_batch_coin_data(batch)

                # Обрабатываем каждую монету с защитой от дублирования
                for symbol, data in batch_data.items():
                    if not self.bot_running or self.bot_mode != 'notification':
                        break

                    if not data:
                        continue

                    # Защита от одновременной обработки одной монеты
                    if symbol in self._processing_coins:
                        continue

                    try:
                        self._processing_coins.add(symbol)
                        await self._process_coin_notification(symbol, data)
                        await asyncio.sleep(0.01)
                    except Exception as e:
                        bot_logger.error(f"Ошибка обработки {symbol}: {e}")
                    finally:
                        self._processing_coins.discard(symbol)

                await asyncio.sleep(config_manager.get('CHECK_BATCH_INTERVAL'))

            await asyncio.sleep(config_manager.get('CHECK_FULL_CYCLE_INTERVAL'))

    async def _cleanup_stale_processes(self):
        """Очистка зависших процессов"""
        current_time = time.time()
        to_remove = []

        for symbol, coin_info in list(self.active_coins.items()):
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
                del self.active_coins[symbol]
                bot_logger.info(f"[CLEANUP] Очищена зависшая монета {symbol}")
            except Exception as e:
                bot_logger.error(f"[CLEANUP] Ошибка очистки {symbol}: {e}")

        # Очистка старых блокировок
        self._processing_coins.clear()

    async def _process_coin_notification(self, symbol: str, data: Dict):
        """Обработка уведомлений монет с полной защитой от дублирования"""
        now = time.time()

        # Проверяем алерты
        try:
            from advanced_alerts import advanced_alert_manager
            advanced_alert_manager.check_coin_alerts(symbol, data)
        except:
            pass

        if data['active']:
            # Монета активна
            if symbol not in self.active_coins:
                # Дополнительная защита от дублирования
                if symbol in self._notification_locks:
                    return

                self._notification_locks.add(symbol)

                try:
                    await self._create_coin_notification(symbol, data, now)
                finally:
                    self._notification_locks.discard(symbol)
            else:
                # Обновляем существующую монету
                await self._update_coin_notification(symbol, data, now)
        else:
            # Монета неактивна - проверяем завершение
            if symbol in self.active_coins:
                coin_info = self.active_coins[symbol]

                # Пропускаем если создается
                if coin_info.get('creating', False):
                    return

                inactivity_timeout = config_manager.get('INACTIVITY_TIMEOUT')
                if now - coin_info['last_active'] > inactivity_timeout:
                    await self._end_coin_activity(symbol, now)

    async def _create_coin_notification(self, symbol: str, data: Dict, now: float):
        """Создает новое уведомление для монеты"""
        bot_logger.info(f"[NOTIFICATION_START] {symbol} - новая активная монета обнаружена")

        # Создаем запись с флагом creating
        self.active_coins[symbol] = {
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

        if msg_id and symbol in self.active_coins:  # Проверяем что запись еще существует
            # Обновляем запись с полученным msg_id
            self.active_coins[symbol].update({
                'msg_id': msg_id,
                'creating': False
            })
            self.message_cache[msg_id] = message
            bot_logger.trade_activity(symbol, "STARTED", f"Volume: ${data['volume']:,.2f}")
            bot_logger.info(f"[NOTIFICATION_SUCCESS] {symbol} - уведомление создано успешно")
        else:
            # Удаляем неудачную запись
            if symbol in self.active_coins:
                del self.active_coins[symbol]
            bot_logger.warning(f"[NOTIFICATION_FAILED] {symbol} - не удалось создать уведомление")

    async def _update_coin_notification(self, symbol: str, data: Dict, now: float):
        """Обновляет существующее уведомление"""
        coin_info = self.active_coins[symbol]

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

            # Проверяем изменения перед обновлением
            cached_message = self.message_cache.get(msg_id)
            if cached_message != new_message:
                await self.edit_message(msg_id, new_message)

    async def _end_coin_activity(self, symbol: str, end_time: float):
        """Завершает активность монеты"""
        if symbol not in self.active_coins:
            return

        coin_info = self.active_coins[symbol]
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
        del self.active_coins[symbol]

    async def _monitoring_mode_loop(self):
        """Цикл режима мониторинга"""
        bot_logger.info("Запущен режим мониторинга")

        # Запускаем процессор очереди сообщений
        await self._start_message_queue_processor()

        # Отправляем начальное сообщение
        initial_text = "🔄 <b>Инициализация мониторинга...</b>"
        self.monitoring_message_id = await self.send_message(initial_text)

        cycle_count = 0
        while self.bot_running and self.bot_mode == 'monitoring':
            cycle_count += 1

            # Проверяем список отслеживания
            watchlist = watchlist_manager.get_all()
            if not watchlist:
                no_coins_text = "❌ <b>Список отслеживания пуст</b>\nДобавьте монеты для мониторинга."
                if self.monitoring_message_id:
                    await self.edit_message(self.monitoring_message_id, no_coins_text)
                await asyncio.sleep(config_manager.get('MONITORING_UPDATE_INTERVAL'))
                continue

            # Получаем данные монет
            try:
                results, failed_coins = await self._fetch_monitoring_data()

                # Обновляем отчет
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
                    from cache_manager import cache_manager
                    cache_manager.clear_expired()

            except Exception as e:
                bot_logger.error(f"Ошибка в цикле мониторинга: {e}")

            await asyncio.sleep(config_manager.get('MONITORING_UPDATE_INTERVAL'))

    async def _fetch_monitoring_data(self):
        """Получает данные для мониторинга"""
        watchlist = list(watchlist_manager.get_all())
        results = []
        failed_coins = []

        batch_size = config_manager.get('CHECK_BATCH_SIZE', 15)
        for batch in self._chunks(watchlist, batch_size):
            if not self.bot_running or self.bot_mode != 'monitoring':
                break

            try:
                batch_data = await api_client.get_batch_coin_data(batch)
                for symbol, coin_data in batch_data.items():
                    if coin_data:
                        results.append(coin_data)
                    else:
                        failed_coins.append(symbol)
            except Exception as e:
                bot_logger.error(f"Ошибка batch получения данных: {e}")
                failed_coins.extend(batch)

            await asyncio.sleep(config_manager.get('CHECK_BATCH_INTERVAL', 0.4))

        return results, failed_coins

    def _format_monitoring_report(self, results: List[Dict], failed_coins: List[str]) -> str:
        """Форматирует отчет мониторинга"""
        results.sort(key=lambda x: x['volume'], reverse=True)

        parts = ["<b>📊 Скальпинг мониторинг (1м данные)</b>\n"]

        vol_thresh = config_manager.get('VOLUME_THRESHOLD')
        spread_thresh = config_manager.get('SPREAD_THRESHOLD')
        natr_thresh = config_manager.get('NATR_THRESHOLD')

        parts.append(
            f"<i>Фильтры: 1м оборот ≥${vol_thresh:,}, "
            f"Спред ≥{spread_thresh}%, NATR ≥{natr_thresh}%</i>\n"
        )

        if failed_coins:
            parts.append(f"⚠ <i>Ошибки: {', '.join(failed_coins[:5])}</i>\n")

        active_coins = [r for r in results if r['active']]
        if active_coins:
            parts.append("<b>🟢 АКТИВНЫЕ:</b>")
            for coin in active_coins[:10]:
                trades_icon = "🔥" if coin.get('has_recent_trades') else "📊"
                parts.append(
                    f"• <b>{coin['symbol']}</b> "
                    f"${coin['volume']:,.0f} | {coin['change']:+.1f}% | "
                    f"{trades_icon}T:{coin['trades']} | S:{coin['spread']:.2f}% | N:{coin['natr']:.2f}%"
                )
            parts.append("")

        inactive_coins = [r for r in results if not r['active']]
        if inactive_coins:
            parts.append("<b>🔴 НЕАКТИВНЫЕ (топ по объёму):</b>")
            for coin in inactive_coins[:8]:
                trades_status = "✅" if coin['trades'] > 0 else "❌"
                parts.append(
                    f"• <b>{coin['symbol']}</b> "
                    f"${coin['volume']:,.0f} | {coin['change']:+.1f}% | "
                    f"{trades_status}T:{coin['trades']} | S:{coin['spread']:.2f}% | N:{coin['natr']:.2f}%"
                )

        parts.append(f"\n📈 Активных: {len(active_coins)}/{len(results)}")

        report = "\n".join(parts)
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
        """Обработка режима уведомлений"""
        if self.bot_running and self.bot_mode == 'notification':
            await update.message.reply_text(
                "✅ Бот уже работает в режиме уведомлений.",
                reply_markup=self.main_keyboard
            )
            return

        await self._stop_current_mode()
        await asyncio.sleep(1.0)

        await update.message.reply_text(
            "✅ <b>Режим уведомлений активирован</b>\n"
            "Вы будете получать уведомления об активных монетах.\n"
            "🚀 <i>Оптимизирован для скальпинга</i>",
            reply_markup=self.main_keyboard,
            parse_mode=ParseMode.HTML
        )

        await asyncio.sleep(0.3)

        self.bot_mode = 'notification'
        self.bot_running = True
        bot_state_manager.set_last_mode('notification')
        self.start_monitoring_loop()

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
        await asyncio.sleep(1.0)

        await update.message.reply_text(
            "✅ <b>Режим мониторинга активирован</b>\n"
            "Сводка будет обновляться автоматически.\n"
            "🚀 <i>Оптимизирован для скальпинга</i>",
            reply_markup=self.main_keyboard,
            parse_mode=ParseMode.HTML
        )

        await asyncio.sleep(0.3)

        self.bot_mode = 'monitoring'
        self.bot_running = True
        bot_state_manager.set_last_mode('monitoring')
        self.start_monitoring_loop()

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

        status_parts.append("\n⚙ <b>Фильтры:</b>")
        status_parts.append(f"• Объём: ${config_manager.get('VOLUME_THRESHOLD'):,}")
        status_parts.append(f"• Спред: {config_manager.get('SPREAD_THRESHOLD')}%")
        status_parts.append(f"• NATR: {config_manager.get('NATR_THRESHOLD')}%")

        await update.message.reply_text(
            "\n".join(status_parts),
            reply_markup=self.main_keyboard,
            parse_mode=ParseMode.HTML
        )

    async def _handle_alerts(self, update: Update):
        """Обработка алертов"""
        await self._stop_current_mode()

        stats = advanced_alert_manager.get_alert_stats()
        active_alerts = advanced_alert_manager.get_active_alerts()
        recent_history = advanced_alert_manager.get_alert_history(5)

        alerts_text = f"🚨 <b>Система алертов:</b>\n\n"
        alerts_text += f"📊 <b>Статистика:</b>\n"
        alerts_text += f"• Всего алертов: {stats['total_alerts']}\n"
        alerts_text += f"• Активных: {stats['active_alerts']}\n"
        alerts_text += f"• Общих срабатываний: {stats['total_triggers']}\n\n"

        if active_alerts:
            alerts_text += f"🔴 <b>Активные алерты ({len(active_alerts)}):</b>\n"
            for alert in active_alerts[:3]:
                alerts_text += f"• {alert['title']} [{alert['severity'].upper()}]\n"
            if len(active_alerts) > 3:
                alerts_text += f"• ... и еще {len(active_alerts) - 3}\n"
            alerts_text += "\n"

        if recent_history:
            alerts_text += f"📋 <b>Последние срабатывания:</b>\n"
            for alert in recent_history:
                time_str = time.strftime("%H:%M", time.localtime(alert['timestamp']))
                alerts_text += f"• {time_str} - {alert['title']} ({alert['symbol']})\n"
        else:
            alerts_text += f"✅ <b>Нет недавних срабатываний</b>\n"

        alerts_text += f"\n💡 Алерты работают автоматически в фоновом режиме"

        await update.message.reply_text(
            alerts_text,
            reply_markup=self.main_keyboard,
            parse_mode=ParseMode.HTML
        )

    async def _handle_reset_settings(self, update: Update):
        """Сброс настроек к значениям по умолчанию"""
        await self._stop_current_mode()

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

        from input_validator import input_validator

        if not input_validator.validate_symbol(text):
            await update.message.reply_text(
                "❌ Некорректный символ. Используйте только буквы и цифры (2-10 символов).\n"
                "Примеры: BTC, ETH, DOGE",
                reply_markup=self.back_keyboard
            )
            return self.ADDING_COIN

        symbol = text.upper().replace("_USDT", "").replace("USDT", "")

        if not symbol or len(symbol) < 2:
            await update.message.reply_text(
                "❌ Некорректный символ. Попробуйте еще раз:",
                reply_markup=self.back_keyboard
            )
            return self.ADDING_COIN

        if watchlist_manager.contains(symbol):
            await update.message.reply_text(
                f"⚠ <b>{symbol}</b> уже в списке отслеживания.",
                reply_markup=self.main_keyboard,
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END

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