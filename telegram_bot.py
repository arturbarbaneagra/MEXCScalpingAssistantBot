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
from websocket_client import ws_client
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
        min_interval = config_manager.get('MESSAGE_RATE_LIMIT')

        if current_time - self.last_message_time < min_interval:
            await asyncio.sleep(min_interval - (current_time - self.last_message_time))

        self.last_message_time = time.time()

    async def send_message(self, text: str, reply_markup=None, parse_mode=ParseMode.HTML) -> Optional[int]:
        """Отправляет сообщение с ограничением частоты"""
        if not self.app or not self.app.bot:
            bot_logger.error("Бот не инициализирован для отправки сообщения")
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
            # Специальная обработка event loop ошибок
            if any(phrase in error_message for phrase in [
                "event loop", "different event loop", "asyncio.locks.event",
                "is bound to a different event loop", "runtimeerror"
            ]):
                bot_logger.debug(f"Event loop ошибка при отправке сообщения: {type(e).__name__}")
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
            bot_logger.error(f"Ошибка редактирования сообщения: {e}")

    async def delete_message(self, message_id: int) -> bool:
        """Удаляет сообщение с улучшенной обработкой event loop"""
        if not message_id or not isinstance(message_id, int) or message_id <= 0:
            bot_logger.debug(f"Некорректный ID сообщения: {message_id}")
            return False

        if not self.app or not self.app.bot:
            bot_logger.debug(f"Приложение или бот не инициализированы для удаления сообщения {message_id}")
            return False

        try:
            # Упрощенное удаление с минимальными проверками
            await asyncio.wait_for(
                self.app.bot.delete_message(chat_id=self.chat_id, message_id=message_id),
                timeout=3.0
            )
            bot_logger.debug(f"Сообщение {message_id} успешно удалено")
            return True

        except asyncio.TimeoutError:
            bot_logger.debug(f"Таймаут удаления сообщения {message_id}")
            return False
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
                "event loop is closed",
                "networkerror",
                "unknown error in http implementation"
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
        """Останавливает текущий режим работы бота с улучшенной обработкой"""
        if self.bot_mode:
            bot_logger.info(f"🛑 Остановка режима: {self.bot_mode}")

            # Сначала останавливаем циклы
            self.bot_running = False

            # WebSocket отключен для стабильности
            # Используем только HTTP API

            # Даем время циклам завершиться
            await asyncio.sleep(0.5)

            # Безопасная очистка сообщений без блокирования
            try:
                # Удаляем сообщение мониторинга если оно есть
                if self.monitoring_message_id:
                    try:
                        await asyncio.wait_for(
                            self.delete_message(self.monitoring_message_id), 
                            timeout=2.0
                        )
                        bot_logger.info("📝 Сообщение мониторинга удалено")
                    except asyncio.TimeoutError:
                        bot_logger.debug("Таймаут удаления сообщения мониторинга")
                    except Exception as e:
                        bot_logger.debug(f"Ошибка удаления сообщения мониторинга: {type(e).__name__}")
                    finally:
                        self.monitoring_message_id = None

                # Удаляем активные уведомления
                if self.active_coins:
                    deleted_count = 0
                    for symbol, coin_data in list(self.active_coins.items()):
                        if coin_data.get('msg_id'):
                            try:
                                await asyncio.wait_for(
                                    self.delete_message(coin_data['msg_id']), 
                                    timeout=1.0
                                )
                                deleted_count += 1
                            except Exception:
                                pass  # Игнорируем ошибки удаления отдельных сообщений
                    
                    if deleted_count > 0:
                        bot_logger.info(f"🗑 Удалено {deleted_count} уведомлений")
                    self.active_coins.clear()

            except Exception as e:
                bot_logger.debug(f"Общая ошибка при очистке сообщений: {type(e).__name__}")
                # Принудительная очистка состояния
                self.monitoring_message_id = None
                self.active_coins.clear()

            self.bot_mode = None
            # Сохраняем состояние
            bot_state_manager.set_last_mode(None)

            # Правильно закрываем API сессию
            try:
                await api_client.close()
                # Дополнительная пауза для завершения всех соединений
                await asyncio.sleep(0.2)
                bot_logger.debug("API сессия корректно закрыта")
            except Exception as e:
                bot_logger.debug(f"Ошибка закрытия API сессии: {e}")

    async def _notification_mode_loop(self):
        """Оптимизированный цикл уведомлений (только HTTP)"""
        bot_logger.info("Запущен оптимизированный режим уведомлений (HTTP только)")

        # Используем только HTTP с максимальной оптимизацией
        # WebSocket отключен для стабильности
        await self._notification_mode_loop_optimized()

        # Основной цикл заменен на _notification_mode_loop_optimized()
        # который вызывается выше

    async def _process_coin_notification(self, symbol: str, data: Dict):
        """Обрабатывает уведомление для монеты - упрощенная логика как в старом боте"""
        now = time.time()
        is_currently_active = symbol in self.active_coins

        # Проверяем алерты для монеты
        advanced_alert_manager.check_coin_alerts(symbol, data)

        if data['active']:
            if not is_currently_active:
                # Новая активная монета
                message = (
                    f"🚨 <b>{symbol}_USDT активен</b>\n"
                    f"🔄 Изм: {data['change']:+.2f}%  🔁 Сделок: {data['trades']}\n"
                    f"📊 Объём: ${data['volume']:,.2f}  NATR: {data['natr']:.2f}%\n"
                    f"⇄ Спред: {data['spread']:.2f}%"
                )
                msg_id = await self.send_message(message)

                if msg_id:
                    self.active_coins[symbol] = {
                        'start': now,
                        'last_active': now,
                        'msg_id': msg_id,
                        'data': data
                    }
                    bot_logger.trade_activity(symbol, "STARTED", f"Volume: ${data['volume']:,.2f}, Trades: {data['trades']}")
            else:
                # Обновляем данные по активной монете
                self.active_coins[symbol]['last_active'] = now
                self.active_coins[symbol]['data'] = data

                # Обновляем сообщение
                message = (
                    f"🚨 <b>{symbol}_USDT активен</b>\n"
                    f"🔄 Изм: {data['change']:+.2f}%  🔁 Сделок: {data['trades']}\n"
                    f"📊 Объём: ${data['volume']:,.2f}  NATR: {data['natr']:.2f}%\n"
                    f"⇄ Спред: {data['spread']:.2f}%"
                )
                await self.edit_message(self.active_coins[symbol]['msg_id'], message)

        elif is_currently_active:
            # Проверяем, не прошло ли время неактивности
            inactivity_timeout = config_manager.get('INACTIVITY_TIMEOUT')
            if now - self.active_coins[symbol]['last_active'] > inactivity_timeout:
                await self._end_coin_activity(symbol, now)

    async def _ws_coin_callback(self, symbol: str, ticker_data: Dict):
        """Callback для обработки WebSocket данных монеты"""
        try:
            # Получаем дополнительные данные только при необходимости
            coin_data = await optimized_api_client.get_optimized_coin_data(symbol)
            if coin_data:
                await self._process_coin_notification(symbol, coin_data)
        except Exception as e:
            bot_logger.debug(f"Ошибка WebSocket callback для {symbol}: {e}")

    async def _process_notification_batch(self, batch: List[str]):
        """Обрабатывает батч монет параллельно"""
        try:
            # Создаем задачи для параллельной обработки
            tasks = []
            for symbol in batch:
                if not self.bot_running or self.bot_mode != 'notification':
                    break
                task = self._process_single_coin_optimized(symbol)
                tasks.append(task)

            # Выполняем все задачи параллельно
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            bot_logger.error(f"Ошибка обработки батча: {e}")

    async def _process_single_coin_optimized(self, symbol: str):
        """Оптимизированная обработка одной монеты"""
        try:
            # Сначала пытаемся получить из оптимизированного API
            coin_data = await optimized_api_client.get_optimized_coin_data(symbol)
            if coin_data:
                await self._process_coin_notification(symbol, coin_data)
        except Exception as e:
            bot_logger.debug(f"Ошибка оптимизированной обработки {symbol}: {e}")

    async def _notification_mode_loop_optimized(self):
        """Максимально оптимизированный HTTP режим"""
        bot_logger.info("Запущен максимально оптимизированный режим уведомлений")

        while self.bot_running and self.bot_mode == 'notification':
            watchlist = watchlist_manager.get_all()
            if not watchlist:
                await asyncio.sleep(1)
                continue

            # Обрабатываем все монеты параллельно с минимальными задержками
            await self._process_all_coins_parallel(watchlist)

            # Минимальная задержка между циклами
            await asyncio.sleep(0.5)  # Было 2 секунды, стало 0.5

    async def _process_all_coins_parallel(self, watchlist):
        """Обрабатывает все монеты параллельно"""
        try:
            # Создаем семафор для ограничения одновременных запросов
            semaphore = asyncio.Semaphore(25)  # Увеличиваем до 25
            
            async def process_coin_limited(symbol):
                async with semaphore:
                    try:
                        # Используем оптимизированный клиент
                        coin_data = await optimized_api_client.get_optimized_coin_data(symbol)
                        if coin_data:
                            await self._process_coin_notification(symbol, coin_data)
                    except Exception as e:
                        bot_logger.debug(f"Ошибка обработки {symbol}: {e}")

            # Запускаем все задачи параллельно
            tasks = [process_coin_limited(symbol) for symbol in watchlist]
            await asyncio.gather(*tasks, return_exceptions=True)
            
        except Exception as e:
            bot_logger.error(f"Ошибка параллельной обработки: {e}")

    async def _end_coin_activity(self, symbol: str, end_time: float):
        """Завершает активность монеты - как в старом боте"""
        coin_info = self.active_coins[symbol]
        duration = end_time - coin_info['start']

        # Удаляем сообщение об активности
        msg_id = coin_info.get('msg_id')
        if msg_id:
            await self.delete_message(msg_id)

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
        del self.active_coins[symbol]

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
        """Оптимизированный цикл мониторинга (только HTTP)"""
        bot_logger.info("Запущен оптимизированный режим мониторинга (HTTP только)")

        # Не используем WebSocket для стабильности
        # Полагаемся только на оптимизированный HTTP API
        
        # Отправляем начальное сообщение
        initial_text = "🔄 <b>Инициализация оптимизированного мониторинга...</b>"
        self.monitoring_message_id = await self.send_message(initial_text)

        cycle_count = 0
        while self.bot_running and self.bot_mode == 'monitoring':
            cycle_count += 1

            # Проверяем список отслеживания
            if not await self._check_watchlist_for_monitoring():
                await asyncio.sleep(5)  # Уменьшили с 20 до 5 секунд
                continue

            # Получаем данные монет с оптимизацией
            results, failed_coins = await self._fetch_monitoring_data_optimized()

            # Обновляем отчет
            await self._update_monitoring_report(results, failed_coins)

            # Периодическая очистка
            await self._periodic_cleanup(cycle_count)

            # Максимально уменьшаем интервал обновления
            await asyncio.sleep(1)  # Было 5 секунд, стало 1 секунда

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

    async def _fetch_monitoring_data_optimized(self) -> tuple:
        """Оптимизированное получение данных для мониторинга"""
        watchlist = watchlist_manager.get_all()
        results = []
        failed_coins = []

        try:
            # Создаем задачи для параллельного получения данных
            tasks = []
            for symbol in watchlist:
                if not self.bot_running or self.bot_mode != 'monitoring':
                    break
                task = optimized_api_client.get_optimized_coin_data(symbol)
                tasks.append((symbol, task))

            # Выполняем все задачи параллельно с ограничением
            semaphore = asyncio.Semaphore(15)  # Максимум 15 одновременных запросов
            
            async def limited_task(symbol, task):
                async with semaphore:
                    try:
                        result = await task
                        return symbol, result
                    except Exception as e:
                        bot_logger.debug(f"Ошибка получения данных {symbol}: {e}")
                        return symbol, None

            # Получаем все результаты
            batch_results = await asyncio.gather(
                *[limited_task(symbol, task) for symbol, task in tasks],
                return_exceptions=True
            )

            # Обрабатываем результаты
            for result in batch_results:
                if isinstance(result, Exception):
                    continue
                    
                symbol, coin_data = result
                if coin_data:
                    results.append(coin_data)
                else:
                    failed_coins.append(symbol)

        except Exception as e:
            bot_logger.error(f"Ошибка оптимизированного получения данных мониторинга: {e}")
            # Fallback на старый метод
            return await self._fetch_monitoring_data()

        return results, failed_coins

    async def _fetch_monitoring_data(self) -> tuple:
        """Получает данные для мониторинга"""
        watchlist = watchlist_manager.get_all()
        results = []
        failed_coins = []

        batch_size = config_manager.get('CHECK_BATCH_SIZE')
        for batch in self._chunks(sorted(watchlist), batch_size):
            if not self.bot_running or self.bot_mode != 'monitoring':
                break

            batch_results, batch_failures = await self._process_monitoring_batch(batch)
            results.extend(batch_results)
            failed_coins.extend(batch_failures)

            await asyncio.sleep(config_manager.get('CHECK_BATCH_INTERVAL'))

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
        """Обработка режима уведомлений"""
        if self.bot_running and self.bot_mode == 'notification':
            await update.message.reply_text(
                "✅ Бот уже работает в режиме уведомлений.",
                reply_markup=self.main_keyboard
            )
            return

        # Останавливаем текущий режим (включая удаление сообщений)
        await self._stop_current_mode()

        # Увеличиваем задержку для полной стабилизации event loop
        await asyncio.sleep(1.0)

        # Дополнительно очищаем сообщение мониторинга, если оно есть
        if self.monitoring_message_id:
            bot_logger.info(f"Принудительная очистка сообщения мониторинга: {self.monitoring_message_id}")
            await self.delete_message(self.monitoring_message_id)
            self.monitoring_message_id = None

        # Запускаем новый режим
        self.bot_mode = 'notification'
        self.bot_running = True
        bot_state_manager.set_last_mode('notification')

        # Отправляем сообщение ПЕРЕД запуском loop чтобы избежать конфликта
        try:
            # Дополнительная проверка event loop перед отправкой
            try:
                current_loop = asyncio.get_running_loop()
                if current_loop.is_closed():
                    bot_logger.debug("Event loop закрыт, пропускаем отправку подтверждения")
                else:
                    await update.message.reply_text(
                        "✅ <b>Режим уведомлений активирован</b>\n"
                        "Вы будете получать уведомления об активных монетах.\n"
                        "🚀 <i>Оптимизирован для скальпинга</i>",
                        reply_markup=self.main_keyboard,
                        parse_mode=ParseMode.HTML
                    )
            except RuntimeError as loop_error:
                if "event loop" in str(loop_error).lower():
                    bot_logger.debug(f"Event loop ошибка при отправке подтверждения: {type(loop_error).__name__}")
                else:
                    raise
        except Exception as e:
            error_msg = str(e).lower()
            if "event loop" in error_msg or "asyncio" in error_msg:
                bot_logger.debug(f"Event loop конфликт при активации уведомлений: {type(e).__name__}")
            else:
                bot_logger.error(f"Ошибка отправки подтверждения: {e}")

        # Запускаем loop после отправки подтверждения
        self.start_monitoring_loop()

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

        # Увеличиваем задержку для полной стабилизации event loop
        await asyncio.sleep(1.0)

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

        # Отправляем сообщение ПЕРЕД запуском loop чтобы избежать конфликта
        try:
            # Дополнительная проверка event loop перед отправкой
            try:
                current_loop = asyncio.get_running_loop()
                if current_loop.is_closed():
                    bot_logger.debug("Event loop закрыт, пропускаем отправку подтверждения")
                else:
                    await update.message.reply_text(
                        "✅ <b>Режим мониторинга активирован</b>\n"
                        "Сводка будет обновляться автоматически.\n"
                        "🚀 <i>Оптимизирован для скальпинга</i>",
                        reply_markup=self.main_keyboard,
                        parse_mode=ParseMode.HTML
                    )
            except RuntimeError as loop_error:
                if "event loop" in str(loop_error).lower():
                    bot_logger.debug(f"Event loop ошибка при отправке подтверждения: {type(loop_error).__name__}")
                else:
                    raise
        except Exception as e:
            error_msg = str(e).lower()
            if "event loop" in error_msg or "asyncio" in error_msg:
                bot_logger.debug(f"Event loop конфликт при активации мониторинга: {type(e).__name__}")
            else:
                bot_logger.error(f"Ошибка отправки подтверждения: {e}")

        # Запускаем loop после отправки подтверждения
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
            bot_logger.debug(f"Ошибка отправки сообщения остановки: {type(e).__name__}")
            # Пробуем отправить через наш метод
            try:
                await self.send_message(
                    "🛑 <b>Бот остановлен</b>",
                    reply_markup=self.main_keyboard
                )
            except Exception:
                bot_logger.debug("Не удалось отправить подтверждение остановки")

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
            error_str = str(error).lower()

            # Игнорируем event loop ошибки
            if any(phrase in error_str for phrase in [
                "event loop", "different event loop", "asyncio.locks.event",
                "is bound to a different event loop", "unknown error in http implementation"
            ]):
                bot_logger.debug(f"Event loop ошибка (игнорируется): {type(error).__name__}")
                return

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