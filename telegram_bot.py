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
        self.message_cache = {}  # Кеш содержимого сообщений для предотвращения дублирования

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
        min_interval = config_manager.get('MESSAGE_RATE_LIMIT')

        if current_time - self.last_message_time < min_interval:
            await asyncio.sleep(min_interval - (current_time - self.last_message_time))

        self.last_message_time = time.time()

    async def send_message(self, text: str, reply_markup=None, parse_mode=ParseMode.HTML) -> Optional[int]:
        """Отправляет сообщение с улучшенной обработкой ошибок"""
        if not self.app or not self.app.bot:
            bot_logger.debug("Бот не инициализирован для отправки сообщения")
            return None

        # Проверяем event loop перед отправкой
        try:
            current_loop = asyncio.get_running_loop()
            if current_loop.is_closed():
                bot_logger.debug("Event loop закрыт, пропускаем отправку сообщения")
                return None
        except RuntimeError:
            bot_logger.debug("Нет активного event loop для отправки сообщения")
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
            # Расширенная обработка различных типов ошибок
            ignored_error_patterns = [
                "event loop", "different event loop", "asyncio.locks.event",
                "is bound to a different event loop", "runtimeerror",
                "unknown error in http implementation", "networkerror"
            ]

            if any(pattern in error_message for pattern in ignored_error_patterns):
                bot_logger.debug(f"[NETWORK] Event loop/сетевая ошибка при отправке: {type(e).__name__}")
                return None
            else:
                bot_logger.error(f"[SEND_ERROR] Ошибка отправки сообщения: {e}")
                return None

    async def edit_message(self, message_id: int, text: str, reply_markup=None):
        """Редактирует сообщение с проверкой на изменения"""
        try:
            await self.app.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            error_message = str(e).lower()
            # Игнорируем ошибку "сообщение не изменилось"
            if "message is not modified" in error_message:
                bot_logger.debug(f"Сообщение {message_id} не изменилось, пропускаем обновление")
                return

            # Обрабатываем другие типы ошибок
            ignored_error_patterns = [
                "message to edit not found",
                "message can't be edited",
                "message is too old",
                "bad request"
            ]

            if any(pattern in error_message for pattern in ignored_error_patterns):
                bot_logger.debug(f"Сообщение {message_id} недоступно для редактирования: {type(e).__name__}")
            else:
                bot_logger.error(f"Ошибка редактирования сообщения {message_id}: {e}")

    async def delete_message(self, message_id: int) -> bool:
        """Удаляет сообщение с улучшенной обработкой event loop"""
        if not message_id or not isinstance(message_id, int) or message_id <= 0:
            bot_logger.debug(f"Некорректный ID сообщения: {message_id}")
            return False

        if not self.app or not self.app.bot:
            bot_logger.debug(f"Приложение или бот не инициализированы для удаления сообщения {message_id}")
            return False

        try:
            # Безопасная проверка event loop
            try:
                # Пытаемся получить текущий loop
                current_loop = asyncio.get_running_loop()
                # Проверяем, что loop не закрыт
                if current_loop.is_closed():
                    bot_logger.debug(f"Event loop закрыт для сообщения {message_id}")
                    return False
            except RuntimeError:
                # Если нет активного loop, пропускаем удаление
                bot_logger.debug(f"Нет активного event loop для удаления сообщения {message_id}")
                return False

            # Выполняем удаление в правильном контексте
            await self.app.bot.delete_message(chat_id=self.chat_id, message_id=message_id)

            # Очищаем кеш для удаленного сообщения
            if message_id in self.message_cache:
                del self.message_cache[message_id]

            bot_logger.debug(f"Сообщение {message_id} успешно удалено")
            return True

        except Exception as e:
            error_message = str(e).lower()
            # Расширенный список ошибок для игнорирования
            ignored_errors = [
                "message to delete not found",
                "message can't be deleted", 
                "message is too old",
                "bad request",
                "not found",
                "event loop",
                "different event loop", 
                "asyncio.locks.event",
                "runtimeerror",
                "is bound to a different event loop",
                "cannot be called from a running event loop",
                "event loop is closed"
            ]

            if any(phrase in error_message for phrase in ignored_errors):
                bot_logger.debug(f"Сообщение {message_id} недоступно для удаления: {type(e).__name__}")
                return False
            else:
                bot_logger.warning(f"Необработанная ошибка удаления сообщения {message_id}: {e}")
                return False

    def _chunks(self, lst: List, size: int):
        """Разбивает список на чанки"""
        for i in range(0, len(lst), size):
            yield lst[i:i + size]

    async def _stop_current_mode(self):
        """Останавливает текущий режим работы бота с полной очисткой состояния"""
        if self.bot_mode:
            bot_logger.info(f"🛑 Остановка режима: {self.bot_mode}")

            # Сначала останавливаем циклы
            self.bot_running = False

            # Увеличиваем время ожидания для полного завершения циклов
            await asyncio.sleep(1.2)

            try:
                # Проверяем event loop перед удалением сообщений
                try:
                    current_loop = asyncio.get_running_loop()
                    if current_loop.is_closed():
                        bot_logger.debug("Event loop закрыт, принудительная очистка состояния")
                        self._force_clear_state()
                        return
                except RuntimeError:
                    bot_logger.debug("Нет активного event loop, принудительная очистка состояния")
                    self._force_clear_state()
                    return

                # Сначала обрабатываем активные уведомления
                if self.active_coins:
                    deleted_count = 0
                    creating_count = 0
                    
                    for symbol, coin_data in list(self.active_coins.items()):
                        if coin_data.get('creating', False):
                            creating_count += 1
                            bot_logger.info(f"🔄 Принудительная остановка создания уведомления для {symbol}")
                            
                        msg_id = coin_data.get('msg_id')
                        if msg_id and isinstance(msg_id, int) and msg_id > 0:
                            success = await self.delete_message(msg_id)
                            if success:
                                deleted_count += 1
                                bot_logger.debug(f"🗑 Удалено уведомление для {symbol} (ID: {msg_id})")
                    
                    if deleted_count > 0:
                        bot_logger.info(f"🗑 Удалено {deleted_count} уведомлений")
                    if creating_count > 0:
                        bot_logger.info(f"🔄 Прервано {creating_count} процессов создания")
                    
                    # Принудительно очищаем все активные монеты
                    self.active_coins.clear()

                # Затем удаляем сообщение мониторинга
                if self.monitoring_message_id:
                    success = await self.delete_message(self.monitoring_message_id)
                    if success:
                        bot_logger.info("📝 Сообщение мониторинга удалено")
                    self.monitoring_message_id = None

            except Exception as e:
                error_message = str(e).lower()
                if "event loop" in error_message or "asyncio" in error_message:
                    bot_logger.debug(f"Event loop ошибка при остановке: {type(e).__name__}")
                else:
                    bot_logger.warning(f"Ошибка при очистке сообщений: {e}")

                # Принудительная очистка состояния
                self._force_clear_state()

            # Полная очистка всех кешей и состояний
            self._force_clear_state()

            # Правильно закрываем API сессию
            try:
                await api_client.close()
                # Увеличиваем паузу для полного закрытия соединений
                await asyncio.sleep(0.3)
                bot_logger.debug("API сессия корректно закрыта")
            except Exception as e:
                bot_logger.debug(f"Ошибка закрытия API сессии: {e}")

    def _force_clear_state(self):
        """Принудительная очистка всех состояний"""
        self.monitoring_message_id = None
        self.active_coins.clear()
        self.message_cache.clear()
        self.bot_mode = None
        
        # Очищаем блокировки уведомлений
        if hasattr(self, '_notification_locks'):
            self._notification_locks.clear()
            
        bot_state_manager.set_last_mode(None)
        bot_logger.debug("🧹 Принудительная очистка состояния выполнена")

    async def _notification_mode_loop(self):
        """Цикл режима уведомлений с периодической очисткой"""
        bot_logger.info("Запущен режим уведомлений")

        cleanup_counter = 0

        while self.bot_running and self.bot_mode == 'notification':
            watchlist = watchlist_manager.get_all()
            if not watchlist:
                await asyncio.sleep(config_manager.get('CHECK_FULL_CYCLE_INTERVAL'))
                continue

            # Периодическая очистка зависших процессов (каждые 10 циклов)
            cleanup_counter += 1
            if cleanup_counter >= 10:
                await self._cleanup_stale_processes()
                cleanup_counter = 0

            batch_size = config_manager.get('CHECK_BATCH_SIZE')
            for batch in self._chunks(list(watchlist), batch_size):
                if not self.bot_running or self.bot_mode != 'notification':
                    break

                # Обрабатываем батч монет (BATCH-режим для максимальной скорости)
                batch_data = await api_client.get_batch_coin_data(batch)
                batch_results = [data for data in batch_data.values() if data is not None]

                # Минимальная задержка только между батчами
                if batch_results:
                    await asyncio.sleep(config_manager.get('COIN_DATA_DELAY'))

                # Обрабатываем каждую монету в батче с защитой от дублирования
                processed_symbols = set()
                valid_results = []
                
                # Фильтруем и дедуплицируем результаты
                for data in batch_results:
                    if not data or 'symbol' not in data:
                        continue
                        
                    symbol = data['symbol']
                    if symbol in processed_symbols:
                        bot_logger.debug(f"[DEDUPE] {symbol} дублируется в батче, пропускаем")
                        continue
                        
                    processed_symbols.add(symbol)
                    valid_results.append(data)

                # Обрабатываем дедуплицированные результаты
                for data in valid_results:
                    if not self.bot_running or self.bot_mode != 'notification':
                        break

                    try:
                        symbol = data['symbol']
                        await self._process_coin_notification(symbol, data)
                        
                        # Небольшая задержка между обработкой монет
                        await asyncio.sleep(0.01)
                        
                    except Exception as e:
                        bot_logger.error(f"Ошибка обработки {symbol}: {e}")

                await asyncio.sleep(config_manager.get('CHECK_BATCH_INTERVAL'))

            await asyncio.sleep(config_manager.get('CHECK_FULL_CYCLE_INTERVAL'))

    async def _cleanup_stale_processes(self):
        """Усиленная очистка зависших процессов создания уведомлений"""
        current_time = time.time()
        stale_coins = []
        creating_coins = []
        orphaned_coins = []

        for symbol, coin_info in list(self.active_coins.items()):
            if coin_info.get('creating', False):
                creation_time = coin_info.get('creation_time', current_time)
                if current_time - creation_time > 15:  # Уменьшили таймаут до 15 секунд
                    stale_coins.append(symbol)
                else:
                    creating_coins.append(symbol)
            elif not coin_info.get('msg_id'):
                # Монета активна, но без msg_id - потенциальный orphan
                orphaned_coins.append(symbol)

        # Очищаем зависшие процессы
        if stale_coins:
            bot_logger.warning(f"[CLEANUP] Найдены зависшие процессы ({len(stale_coins)}): {', '.join(stale_coins[:5])}")
            for symbol in stale_coins:
                try:
                    coin_info = self.active_coins.get(symbol)
                    if coin_info:
                        msg_id = coin_info.get('msg_id')
                        if msg_id and isinstance(msg_id, int) and msg_id > 0:
                            await self.delete_message(msg_id)
                        del self.active_coins[symbol]
                        bot_logger.info(f"[CLEANUP] Удален зависший процесс для {symbol}")
                except Exception as e:
                    bot_logger.error(f"[CLEANUP] Ошибка очистки {symbol}: {e}")
                    # Принудительное удаление в случае ошибки
                    try:
                        if symbol in self.active_coins:
                            del self.active_coins[symbol]
                    except:
                        pass

        # Очищаем orphaned монеты
        if orphaned_coins:
            bot_logger.warning(f"[CLEANUP] Найдены orphaned монеты ({len(orphaned_coins)}): {', '.join(orphaned_coins[:3])}")
            for symbol in orphaned_coins:
                try:
                    del self.active_coins[symbol]
                    bot_logger.info(f"[CLEANUP] Удалена orphaned монета {symbol}")
                except Exception as e:
                    bot_logger.error(f"[CLEANUP] Ошибка очистки orphaned {symbol}: {e}")

        # Очищаем старые блокировки
        if hasattr(self, '_notification_locks'):
            expired_locks = []
            for symbol, lock_time in list(self._notification_locks.items()):
                if current_time - lock_time > 5.0:  # Блокировки старше 5 секунд
                    expired_locks.append(symbol)
            
            for symbol in expired_locks:
                try:
                    del self._notification_locks[symbol]
                    bot_logger.debug(f"[CLEANUP] Очищена устаревшая блокировка для {symbol}")
                except:
                    pass

        # Очищаем старые метки времени обработки
        if hasattr(self, '_processing_timestamps'):
            expired_timestamps = []
            for symbol, timestamp in list(self._processing_timestamps.items()):
                if current_time - timestamp > 10.0:  # Метки старше 10 секунд
                    expired_timestamps.append(symbol)
            
            for symbol in expired_timestamps:
                try:
                    del self._processing_timestamps[symbol]
                    bot_logger.debug(f"[CLEANUP] Очищена устаревшая метка времени для {symbol}")
                except:
                    pass

        # Информация о текущем состоянии
        if creating_coins:
            bot_logger.debug(f"[STATUS] Монеты в процессе создания ({len(creating_coins)}): {', '.join(creating_coins[:3])}")

        # Общая статистика
        total_active = len(self.active_coins)
        if total_active > 0:
            bot_logger.debug(f"[STATUS] Всего активных монет: {total_active}")

        # Дополнительная очистка кеша сообщений
        if len(self.message_cache) > 100:
            # Оставляем только последние 50 записей
            items = list(self.message_cache.items())
            self.message_cache = dict(items[-50:])
            bot_logger.debug(f"[CLEANUP] Очищен кеш сообщений, оставлено 50 записей")

    async def _process_coin_notification(self, symbol: str, data: Dict):
        """Обрабатывает уведомление для монеты с усиленной защитой от дублирования"""
        now = time.time()

        # Усиленная система блокировок
        if not hasattr(self, '_notification_locks'):
            self._notification_locks = {}
        if not hasattr(self, '_processing_timestamps'):
            self._processing_timestamps = {}
        
        # Проверяем, не обрабатывали ли мы эту монету недавно (защита от спама)
        last_processed = self._processing_timestamps.get(symbol, 0)
        if now - last_processed < 0.5:  # Минимальный интервал 0.5 секунды
            bot_logger.debug(f"[COOLDOWN] {symbol} обработана недавно, пропускаем")
            return
            
        # Проверяем блокировку
        if symbol in self._notification_locks:
            lock_time = self._notification_locks[symbol]
            if now - lock_time < 2.0:  # Блокировка на 2 секунды
                bot_logger.debug(f"[LOCKED] {symbol} заблокована другим процессом")
                return
            else:
                # Старая блокировка, удаляем
                del self._notification_locks[symbol]
            
        # Устанавливаем блокировку и отметку времени
        self._notification_locks[symbol] = now
        self._processing_timestamps[symbol] = now

        try:
            # Проверяем алерты для монеты
            advanced_alert_manager.check_coin_alerts(symbol, data)

            if data['active']:
                # Новая активность или обновление существующей
                if symbol in self.active_coins:
                    # Монета уже активна - только обновляем данные
                    coin_info = self.active_coins[symbol]

                    # Строгая проверка процесса создания
                    if coin_info.get('creating', False):
                        creation_time = coin_info.get('creation_time', now)
                        if now - creation_time > 15:  # Уменьшили таймаут
                            bot_logger.warning(f"[TIMEOUT_UPDATE] Принудительная очистка зависшего процесса {symbol}")
                            try:
                                # Попытаемся удалить сообщение если есть msg_id
                                msg_id = coin_info.get('msg_id')
                                if msg_id and isinstance(msg_id, int) and msg_id > 0:
                                    await self.delete_message(msg_id)
                                del self.active_coins[symbol]
                            except Exception as e:
                                bot_logger.error(f"[CLEANUP_ERROR] Ошибка очистки {symbol}: {e}")
                                # Принудительное удаление
                                if symbol in self.active_coins:
                                    del self.active_coins[symbol]
                        else:
                            bot_logger.debug(f"[SKIP] {symbol} в процессе создания ({now - creation_time:.1f}s)")
                        return

                    # Обновляем данные существующей активной монеты
                    coin_info['last_active'] = now
                    coin_info['data'] = data

                    # Обновляем сообщение только если есть валидный msg_id
                    msg_id = coin_info.get('msg_id')
                    if msg_id and isinstance(msg_id, int) and msg_id > 0:
                        new_message = (
                            f"🚨 <b>{symbol}_USDT активен</b>\n"
                            f"🔄 Изм: {data['change']:+.2f}%  🔁 Сделок: {data['trades']}\n"
                            f"📊 Объём: ${data['volume']:,.2f}  NATR: {data['natr']:.2f}%\n"
                            f"⇄ Спред: {data['spread']:.2f}%"
                        )

                        # Проверяем, изменилось ли содержимое
                        cached_message = self.message_cache.get(msg_id)
                        if cached_message != new_message:
                            await self.edit_message(msg_id, new_message)
                            self.message_cache[msg_id] = new_message
                            bot_logger.debug(f"[UPDATE] {symbol} сообщение обновлено")

                else:
                    # ТРОЙНАЯ ПРОВЕРКА: убеждаемся что монеты действительно нет в активных
                    if symbol in self.active_coins:
                        bot_logger.debug(f"[RACE_DETECTED] {symbol} появилась в активных во время обработки")
                        return

                    # Новая активность - создаем уведомление
                    current_time = time.time()
                    unique_id = f"{symbol}_{current_time}_{id(asyncio.current_task())}"

                    # Создаем запись с блокировкой
                    self.active_coins[symbol] = {
                        'start': current_time,
                        'last_active': current_time,
                        'msg_id': None,
                        'data': data,
                        'creating': True,
                        'lock_id': unique_id,
                        'creation_time': current_time
                    }

                    bot_logger.info(f"[CREATE] Создание уведомления для {symbol} с ID {unique_id}")

                    # Формируем и отправляем сообщение
                    message = (
                        f"🚨 <b>{symbol}_USDT активен</b>\n"
                        f"🔄 Изм: {data['change']:+.2f}%  🔁 Сделок: {data['trades']}\n"
                        f"📊 Объём: ${data['volume']:,.2f}  NATR: {data['natr']:.2f}%\n"
                        f"⇄ Спред: {data['spread']:.2f}%"
                    )

                    try:
                        msg_id = await self.send_message(message)

                        # Проверяем, что монета все еще в процессе создания и принадлежит нам
                        if symbol not in self.active_coins:
                            bot_logger.warning(f"[RACE_LOST] {symbol} исчезла во время создания")
                            return
                            
                        current_coin_info = self.active_coins[symbol]
                        if current_coin_info.get('lock_id') != unique_id:
                            bot_logger.warning(f"[LOCK_MISMATCH] {symbol} обрабатывается другим процессом")
                            return

                        if msg_id and isinstance(msg_id, int) and msg_id > 0:
                            # Успешно отправили
                            self.active_coins[symbol].update({
                                'msg_id': msg_id,
                                'creating': False,
                                'lock_id': None
                            })
                            self.message_cache[msg_id] = message
                            bot_logger.trade_activity(symbol, "STARTED", f"Volume: ${data['volume']:,.2f}")
                        else:
                            # Не удалось отправить
                            bot_logger.warning(f"[FAIL] Не удалось отправить уведомление для {symbol}")
                            if symbol in self.active_coins and self.active_coins[symbol].get('lock_id') == unique_id:
                                del self.active_coins[symbol]

                    except Exception as e:
                        bot_logger.error(f"[ERROR] Ошибка создания уведомления для {symbol}: {e}")
                        if symbol in self.active_coins and self.active_coins[symbol].get('lock_id') == unique_id:
                            del self.active_coins[symbol]

            else:
                # Монета неактивна - проверяем на завершение активности
                if symbol in self.active_coins:
                    coin_info = self.active_coins[symbol]

                    # НЕ завершаем активность для монет в процессе создания
                    if coin_info.get('creating', False):
                        creation_time = coin_info.get('creation_time', now)
                        if now - creation_time > 15:
                            bot_logger.warning(f"[TIMEOUT] Принудительное завершение создания {symbol}")
                            try:
                                msg_id = coin_info.get('msg_id')
                                if msg_id and isinstance(msg_id, int) and msg_id > 0:
                                    await self.delete_message(msg_id)
                                del self.active_coins[symbol]
                            except Exception as e:
                                bot_logger.error(f"[TIMEOUT_CLEANUP_ERROR] {symbol}: {e}")
                                if symbol in self.active_coins:
                                    del self.active_coins[symbol]
                        return

                    # Проверяем таймаут неактивности
                    inactivity_timeout = config_manager.get('INACTIVITY_TIMEOUT')
                    if now - coin_info['last_active'] > inactivity_timeout:
                        await self._end_coin_activity(symbol, now)

        finally:
            # Снимаем блокировку через небольшую задержку
            await asyncio.sleep(0.1)
            if symbol in self._notification_locks:
                del self._notification_locks[symbol]

    async def _end_coin_activity(self, symbol: str, end_time: float):
        """Завершает активность монеты с улучшенной обработкой"""
        if symbol not in self.active_coins:
            bot_logger.debug(f"[END] {symbol} уже не в активных монетах")
            return

        coin_info = self.active_coins[symbol]

        # Не завершаем активность для монет в процессе создания
        if coin_info.get('creating', False):
            creation_time = coin_info.get('creation_time', end_time)
            if end_time - creation_time > 30:  # Таймаут создания
                bot_logger.warning(f"[FORCE_END] Принудительное завершение зависшего процесса {symbol}")
            else:
                bot_logger.debug(f"[SKIP_END] {symbol} в процессе создания, откладываем завершение")
                return

        duration = end_time - coin_info['start']

        bot_logger.info(f"[END] Завершение активности {symbol}, длительность: {duration:.1f}с")

        # Удаляем сообщение об активности только если есть валидный ID
        msg_id = coin_info.get('msg_id')
        if msg_id and isinstance(msg_id, int) and msg_id > 0:
            success = await self.delete_message(msg_id)
            if success:
                bot_logger.debug(f"[DELETE] Сообщение {msg_id} для {symbol} удалено")
            else:
                bot_logger.debug(f"[DELETE_FAIL] Не удалось удалить сообщение {msg_id} для {symbol}")

        # Отправляем сообщение о завершении только если активность была >= 60 секунд
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
        try:
            del self.active_coins[symbol]
            bot_logger.debug(f"[CLEANUP] {symbol} удален из активных монет")
        except KeyError:
            bot_logger.debug(f"[CLEANUP] {symbol} уже был удален из активных монет")

    def _format_coin_message(self, data: Dict, status: str) -> str:
        """Форматирует сообщение о монете"""
        # Время последнего обновления
        update_time = time.strftime("%H:%M:%S", time.localtime())

        # Индикатор активности сделок
        trades_indicator = "🟢" if data['trades'] > 0 else "🔴"

        # Индикатор недавних сделок
        recent_trades_indicator = ""
        if data.get('has_recent_trades'):
            recent_trades_indicator = " 🔥"

        return (
            f"{status} <b>{data['symbol']}_USDT</b>{recent_trades_indicator}\n"
            f"💰 Цена: ${data['price']:.6f}\n"
            f"🔄 1м изменение: {data['change']:+.2f}%\n"
            f"📊 1м оборот: ${data['volume']:,.2f}\n"
            f"📈 1м NATR: {data['natr']:.2f}%\n"
            f"⇄ Спред: {data['spread']:.2f}%\n"
            f"{trades_indicator} 1м сделок: {data['trades']}\n"
            f"⏰ Обновлено: {update_time}"
        )

    async def _monitoring_mode_loop(self):
        """Цикл режима мониторинга - рефакторированная версия"""
        bot_logger.info("Запущен режим мониторинга")

        # Отправляем начальное сообщение
        initial_text = "🔄 <b>Инициализация мониторинга...</b>"
        self.monitoring_message_id = await self.send_message(initial_text)

        cycle_count = 0
        while self.bot_running and self.bot_mode == 'monitoring':
            cycle_count += 1

            # Проверяем список отслеживания
            if not await self._check_watchlist_for_monitoring():
                await asyncio.sleep(config_manager.get('MONITORING_UPDATE_INTERVAL'))
                continue

            # Получаем данные монет
            results, failed_coins = await self._fetch_monitoring_data()

            # Обновляем отчет
            await self._update_monitoring_report(results, failed_coins)

            # Периодическая очистка
            await self._periodic_cleanup(cycle_count)

            await asyncio.sleep(config_manager.get('MONITORING_UPDATE_INTERVAL'))

        # Очищаем при остановке
        await self._cleanup_monitoring_mode()

    async def _check_watchlist_for_monitoring(self) -> bool:
        """Проверяет список отслеживания для мониторинга"""
        watchlist = watchlist_manager.get_all()
        if not watchlist:
            no_coins_text = "❌ <b>Список отслеживания пуст</b>\nДобавьте монеты для мониторинга."
            if self.monitoring_message_id:
                await self.edit_message(self.monitoring_message_id, no_coins_text)
            return False
        return True

    async def _fetch_monitoring_data(self):
        """Получает данные для мониторинга (BATCH-оптимизированная версия)"""
        watchlist = list(watchlist_manager.get_all())
        results = []
        failed_coins = []

        # Обрабатываем порциями для максимальной скорости
        batch_size = config_manager.get('CHECK_BATCH_SIZE', 15)

        for batch in self._chunks(watchlist, batch_size):
            if not self.bot_running or self.bot_mode != 'monitoring':
                break

            try:
                # Получаем данные всего батча за один раз
                batch_data = await api_client.get_batch_coin_data(batch)

                for symbol, coin_data in batch_data.items():
                    if coin_data:
                        results.append(coin_data)
                    else:
                        failed_coins.append(symbol)

            except Exception as e:
                bot_logger.error(f"Ошибка batch получения данных: {e}")
                # Fallback - по одной монете
                for symbol in batch:
                    try:
                        coin_data = await api_client.get_coin_data(symbol)
                        if coin_data:
                            results.append(coin_data)
                        else:
                            failed_coins.append(symbol)
                    except Exception as sym_e:
                        bot_logger.error(f"Ошибка получения данных {symbol}: {sym_e}")
                        failed_coins.append(symbol)

            # Пауза между батчами (значительно уменьшена)
            await asyncio.sleep(config_manager.get('CHECK_BATCH_INTERVAL', 0.4))

        return results, failed_coins

    async def _process_monitoring_batch(self, batch) -> tuple:
        """Обрабатывает батч символов для мониторинга"""
        results = []
        failed_coins = []

        try:
            symbols_batch = list(batch)
            ticker_results = await api_client.get_multiple_tickers_batch(symbols_batch)

            for symbol in symbols_batch:
                ticker_data = ticker_results.get(symbol)
                if ticker_data:
                    try:
                        coin_data = await api_client.get_coin_data(symbol)
                        if coin_data:
                            results.append(coin_data)
                        else:
                            failed_coins.append(symbol)
                    except Exception as e:
                        bot_logger.error(f"Ошибка получения данных для {symbol}: {e}")
                        failed_coins.append(symbol)
                else:
                    failed_coins.append(symbol)

        except Exception as e:
            bot_logger.error(f"Ошибка обработки батча мониторинга: {e}")
            # Fallback на старый метод
            for symbol in batch:
                try:
                    coin_data = await api_client.get_coin_data(symbol)
                    if coin_data:
                        results.append(coin_data)
                    else:
                        failed_coins.append(symbol)
                except Exception as e:
                    bot_logger.error(f"Ошибка получения данных для {symbol}: {e}")
                    failed_coins.append(symbol)

        return results, failed_coins

    async def _update_monitoring_report(self, results: list, failed_coins: list):
        """Обновляет отчет мониторинга"""
        if results:
            report = self._format_monitoring_report(results, failed_coins)
            if self.monitoring_message_id:
                await self.edit_message(self.monitoring_message_id, report)
            else:
                self.monitoring_message_id = await self.send_message(report)

    async def _periodic_cleanup(self, cycle_count: int):
        """Выполняет периодическую очистку памяти"""
        if cycle_count % 50 == 0:
            import gc
            gc.collect()

            from cache_manager import cache_manager
            from metrics_manager import metrics_manager
            cache_manager.clear_expired()
            metrics_manager.cleanup_old_metrics()

            bot_logger.debug(f"Очистка памяти, кеша и метрик после {cycle_count} циклов")

    async def _cleanup_monitoring_mode(self):
        """Очищает ресурсы при остановке мониторинга"""
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
            f"<i>Фильтры: 1м оборот ≥${vol_thresh:,}, "
            f"Спред ≥{spread_thresh}%, NATR ≥{natr_thresh}%</i>\n"
        )

        if failed_coins:
            parts.append(f"⚠ <i>Ошибки: {', '.join(failed_coins[:5])}</i>\n")

        # Показываем активные монеты
        active_coins = [r for r in results if r['active']]
        if active_coins:
            parts.append("<b>🟢 АКТИВНЫЕ:</b>")
            for coin in active_coins[:10]:  # Показываем только первые 10
                trades_icon = "🔥" if coin.get('has_recent_trades') else "📊"
                parts.append(
                    f"• <b>{coin['symbol']}</b> "
                    f"${coin['volume']:,.0f} | {coin['change']:+.1f}% | "
                    f"{trades_icon}T:{coin['trades']} | S:{coin['spread']:.2f}% | N:{coin['natr']:.2f}%"
                )
            parts.append("")

        # Показываем неактивные монеты (топ по объему)
        inactive_coins = [r for r in results if not r['active']]
        if inactive_coins:
            parts.append("<b>🔴 НЕАКТИВНЫЕ (топ по объёму):</b>")
            for coin in inactive_coins[:8]:  # Показываем больше неактивных
                trades_status = "✅" if coin['trades'] > 0 else "❌"
                parts.append(
                    f"• <b>{coin['symbol']}</b> "
                    f"${coin['volume']:,.0f} | {coin['change']:+.1f}% | "
                    f"{trades_status}T:{coin['trades']} | S:{coin['spread']:.2f}% | N:{coin['natr']:.2f}%"
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
        """Обработка режима уведомлений с улучшенной стабильностью"""
        if self.bot_running and self.bot_mode == 'notification':
            await update.message.reply_text(
                "✅ Бот уже работает в режиме уведомлений.",
                reply_markup=self.main_keyboard
            )
            return

        # Останавливаем текущий режим с полной очисткой
        await self._stop_current_mode()

        # Увеличиваем задержку для полной стабилизации
        await asyncio.sleep(1.5)

        # Дополнительная проверка чистоты состояния
        if self.active_coins or self.monitoring_message_id:
            bot_logger.warning("Обнаружены остатки предыдущего режима, выполняем дополнительную очистку")
            self._force_clear_state()
            await asyncio.sleep(0.5)

        # Отправляем подтверждение СНАЧАЛА
        try:
            await update.message.reply_text(
                "✅ <b>Режим уведомлений активирован</b>\n"
                "Вы будете получать уведомления об активных монетах.\n"
                "🚀 <i>Оптимизирован для скальпинга</i>",
                reply_markup=self.main_keyboard,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            bot_logger.error(f"Ошибка отправки подтверждения активации: {e}")

        # Небольшая пауза после отправки подтверждения
        await asyncio.sleep(0.3)

        # Запускаем новый режим
        self.bot_mode = 'notification'
        self.bot_running = True
        bot_state_manager.set_last_mode('notification')

        # Запускаем loop после всех подготовок
        self.start_monitoring_loop()
        
        bot_logger.info("🔔 Режим уведомлений успешно активирован")

    async def _handle_monitoring_mode(self, update: Update):
        """Обработка режима мониторинга с улучшенной стабильностью"""
        if self.bot_running and self.bot_mode == 'monitoring':
            await update.message.reply_text(
                "✅ Бот уже работает в режиме мониторинга.",
                reply_markup=self.main_keyboard
            )
            return

        # Останавливаем текущий режим с полной очисткой
        await self._stop_current_mode()

        # Увеличиваем задержку для полной стабилизации
        await asyncio.sleep(1.5)

        # Дополнительная проверка чистоты состояния
        if self.active_coins or self.monitoring_message_id:
            bot_logger.warning("Обнаружены остатки предыдущего режима, выполняем дополнительную очистку")
            self._force_clear_state()
            await asyncio.sleep(0.5)

        # Отправляем подтверждение СНАЧАЛА
        try:
            await update.message.reply_text(
                "✅ <b>Режим мониторинга активирован</b>\n"
                "Сводка будет обновляться автоматически.\n"
                "🚀 <i>Оптимизирован для скальпинга</i>",
                reply_markup=self.main_keyboard,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            bot_logger.error(f"Ошибка отправки подтверждения активации: {e}")

        # Небольшая пауза после отправки подтверждения
        await asyncio.sleep(0.3)

        # Запускаем новый режим
        self.bot_mode = 'monitoring'
        self.bot_running = True
        bot_state_manager.set_last_mode('monitoring')

        # Запускаем loop после всех подготовок
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

    async def _handle_alerts(self, update: Update):
        """Обработка алертов"""
        await self._stop_current_mode()

        # Получаем статистику алертов
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
            for alert in active_alerts[:3]:  # Показываем только первые 3
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

        # Валидируем и нормализуем символ
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

    async def _track_coin_activity(self, symbol: str, data: Dict):
        """Отслеживает активность монеты с thread-safe операциями"""
        now = time.time()

        # Thread-safe операция с блокировкой
        if not hasattr(self, '_coins_lock'):
            self._coins_lock = asyncio.Lock()

        async with self._coins_lock:
            if symbol not in self.active_coins:
                # Создаем новую запись только если монета действительно активна
                if not data.get('active', False):
                    return

                # Проверяем, что это не ложное срабатывание
                if data.get('volume', 0) < config_manager.get('VOLUME_THRESHOLD') * 0.8:
                    return

                # Дополнительная проверка качества данных
                if (data.get('spread', 0) < config_manager.get('SPREAD_THRESHOLD') * 0.5 or
                    data.get('natr', 0) < config_manager.get('NATR_THRESHOLD') * 0.5):
                    return

                self.active_coins[symbol] = {
                    'start_time': now,
                    'last_active': now,
                    'initial_data': data.copy(),
                    'notification_sent': False,
                    'creating': True,  # Флаг процесса создания
                    'creation_time': now,
                    'update_count': 1
                }

# Глобальный экземпляр бота
telegram_bot = TradingTelegramBot()