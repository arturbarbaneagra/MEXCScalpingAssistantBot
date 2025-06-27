"""
Модуль режима уведомлений
Отвечает за обработку активных монет и отправку уведомлений
"""

import asyncio
import time
from typing import Dict, List, Set
from logger import bot_logger
from config import config_manager
from api_client import api_client
from watchlist_manager import watchlist_manager


class NotificationMode:
    def __init__(self, telegram_bot):
        self.bot = telegram_bot
        self.running = False
        self.active_coins: Dict[str, Dict] = {}
        self.processing_coins: Set[str] = set()
        self.notification_locks: Set[str] = set()
        self.task = None

    async def start(self):
        """Запуск режима уведомлений"""
        if self.running:
            bot_logger.warning("Режим уведомлений уже запущен")
            return

        self.running = True
        self.active_coins.clear()
        self.processing_coins.clear()
        self.notification_locks.clear()

        bot_logger.info("🔔 Запуск режима уведомлений")
        self.task = asyncio.create_task(self._notification_loop())

        await self.bot.send_message(
            "✅ <b>Режим уведомлений активирован</b>\n"
            "🚀 <i>Оптимизирован для скальпинга</i>\n"
            "Вы будете получать уведомления об активных монетах."
        )

    async def stop(self):
        """Остановка режима уведомлений"""
        if not self.running:
            return

        self.running = False

        # Отменяем основную задачу
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await asyncio.wait_for(self.task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        # Удаляем все активные уведомления
        deleted_count = 0
        for symbol, coin_data in list(self.active_coins.items()):
            msg_id = coin_data.get('msg_id')
            if msg_id and isinstance(msg_id, int) and msg_id > 0:
                await self.bot.delete_message(msg_id)
                deleted_count += 1

        if deleted_count > 0:
            bot_logger.info(f"🗑 Удалено {deleted_count} уведомлений")

        # Очищаем состояние
        self.active_coins.clear()
        self.processing_coins.clear()
        self.notification_locks.clear()
        self.task = None

    def _chunks(self, lst: List, size: int):
        """Разбивает список на чанки"""
        for i in range(0, len(lst), size):
            yield lst[i:i + size]

    async def _notification_loop(self):
        """Основной цикл режима уведомлений"""
        cleanup_counter = 0

        while self.running:
            try:
                watchlist = watchlist_manager.get_all()
                if not watchlist:
                    await asyncio.sleep(config_manager.get('CHECK_FULL_CYCLE_INTERVAL'))
                    continue

                # Периодическая очистка
                cleanup_counter += 1
                if cleanup_counter >= 10:
                    await self._cleanup_stale_processes()
                    # Проверяем неактивные сессии
                    try:
                        from session_recorder import session_recorder
                        session_recorder.check_inactive_sessions(self.active_coins)
                    except Exception as e:
                        bot_logger.debug(f"Ошибка проверки сессий: {e}")
                    cleanup_counter = 0

                batch_size = config_manager.get('CHECK_BATCH_SIZE')
                for batch in self._chunks(list(watchlist), batch_size):
                    if not self.running:
                        break

                    # Получаем данные батча
                    batch_data = await api_client.get_batch_coin_data(batch)

                    # Обрабатываем каждую монету
                    for symbol, data in batch_data.items():
                        if not self.running:
                            break

                        if not data:
                            continue

                        # Защита от одновременной обработки
                        if symbol in self.processing_coins:
                            continue

                        try:
                            self.processing_coins.add(symbol)
                            await self._process_coin_notification(symbol, data)
                        except Exception as e:
                            bot_logger.error(f"Ошибка обработки {symbol}: {e}")
                        finally:
                            self.processing_coins.discard(symbol)

                        await asyncio.sleep(0.01)

                    await asyncio.sleep(config_manager.get('CHECK_BATCH_INTERVAL'))

                await asyncio.sleep(config_manager.get('CHECK_FULL_CYCLE_INTERVAL'))

            except asyncio.CancelledError:
                break
            except Exception as e:
                bot_logger.error(f"Ошибка в цикле уведомлений: {e}")
                await asyncio.sleep(1.0)

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
        self.processing_coins.clear()

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
            if symbol not in self.active_coins:
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
        if not self.running:
            return

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
        msg_id = await self.bot.send_message(message)

        if msg_id and symbol in self.active_coins:
            # Обновляем запись с полученным msg_id
            self.active_coins[symbol].update({
                'msg_id': msg_id,
                'creating': False
            })
            bot_logger.trade_activity(symbol, "STARTED", f"Volume: ${data['volume']:,.2f}")
            bot_logger.info(f"[NOTIFICATION_SUCCESS] {symbol} - уведомление создано успешно")
        else:
            # Удаляем неудачную запись
            if symbol in self.active_coins:
                del self.active_coins[symbol]
            bot_logger.warning(f"[NOTIFICATION_FAILED] {symbol} - не удалось создать уведомление")

    async def _update_coin_notification(self, symbol: str, data: Dict, now: float):
        """Обновляет существующее уведомление"""
        if not self.running:
            return

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

            await self.bot.edit_message(msg_id, new_message)

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
            await self.bot.delete_message(msg_id)

        # Отправляем сообщение о завершении если активность была >= 60 секунд
        if duration >= 60:
            duration_min = int(duration // 60)
            duration_sec = int(duration % 60)
            end_message = (
                f"✅ <b>{symbol}_USDT завершил активность</b>\n"
                f"⏱ Длительность: {duration_min} мин {duration_sec} сек"
            )
            await self.bot.send_message(end_message)
            bot_logger.trade_activity(symbol, "ENDED", f"Duration: {duration_min}m {duration_sec}s")

        # Удаляем из активных монет
        del self.active_coins[symbol]

    def get_stats(self):
        """Возвращает статистику режима"""
        return {
            'active': self.running,
            'active_coins_count': len(self.active_coins),
            'processing_coins_count': len(self.processing_coins),
            'active_coins': list(self.active_coins.keys())
        }