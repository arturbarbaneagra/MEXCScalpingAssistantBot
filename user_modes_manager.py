"""
Упрощенный менеджер режимов бота для каждого пользователя
Каждый пользователь независимо запускает/останавливает свой мониторинг
"""

import asyncio
import time
from typing import Dict, Optional, Any, List
from logger import bot_logger
from user_manager import user_manager


class UserBotMode:
    """Режим бота для конкретного пользователя"""

    def __init__(self, chat_id: str, bot_instance):
        self.chat_id = chat_id
        self.bot = bot_instance
        self.running = False
        self.start_time = 0
        self.active_coins = {}
        self.monitoring_message_id = None
        self.task = None

    async def start(self) -> bool:
        """Запускает режим мониторинга для пользователя"""
        if self.running:
            return False

        user_watchlist = user_manager.get_user_watchlist(self.chat_id)
        if not user_watchlist:
            return False

        self.running = True
        self.start_time = time.time()
        self.active_coins.clear()

        # Отправляем начальное сообщение
        initial_text = f"🔄 <b>Ваш мониторинг запущен</b>\nОтслеживается {len(user_watchlist)} монет"
        self.monitoring_message_id = await self.bot._send_personal_message(self.chat_id, initial_text)

        # Запускаем цикл мониторинга
        self.task = asyncio.create_task(self._monitoring_loop())

        bot_logger.info(f"Мониторинг запущен для пользователя {self.chat_id}")
        return True

    async def stop(self) -> bool:
        """Останавливает режим мониторинга"""
        if not self.running:
            return False

        self.running = False

        # Останавливаем задачу
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

        # Удаляем активные уведомления
        for coin_data in list(self.active_coins.values()):
            msg_id = coin_data.get('msg_id')
            if msg_id:
                await self.bot._delete_personal_message(self.chat_id, msg_id)

        # Удаляем сообщение мониторинга
        if self.monitoring_message_id:
            await self.bot._delete_personal_message(self.chat_id, self.monitoring_message_id)

        # Отправляем уведомление об остановке
        stop_text = "🛑 <b>Ваш мониторинг остановлен</b>"
        await self.bot._send_personal_message(self.chat_id, stop_text)

        self.active_coins.clear()
        self.monitoring_message_id = None

        bot_logger.info(f"Мониторинг остановлен для пользователя {self.chat_id}")
        return True

    async def _monitoring_loop(self):
        """Цикл мониторинга для пользователя"""
        cycle_count = 0

        while self.running:
            try:
                cycle_count += 1

                # Получаем список монет пользователя
                user_watchlist = user_manager.get_user_watchlist(self.chat_id)
                if not user_watchlist:
                    await asyncio.sleep(5)
                    continue

                # Получаем данные монет
                results, failed_coins = await self.bot._fetch_user_data(user_watchlist, self.chat_id)

                # Обрабатываем уведомления
                for coin_data in results:
                    if not self.running:
                        break

                    symbol = coin_data['symbol']
                    await self._process_notification(symbol, coin_data)

                # Обновляем отчет мониторинга
                if results:
                    report = self._format_report(results, failed_coins)
                    if self.monitoring_message_id:
                        self.monitoring_message_id = await self.bot._edit_personal_message(self.chat_id, self.monitoring_message_id, report)

                await asyncio.sleep(10)  # Интервал обновления

            except asyncio.CancelledError:
                break
            except Exception as e:
                bot_logger.error(f"Ошибка в цикле мониторинга {self.chat_id}: {e}")
                await asyncio.sleep(2)

    async def _process_notification(self, symbol: str, data: Dict):
        """Обработка уведомлений"""
        now = time.time()

        # Получаем пользовательские фильтры
        user_config = user_manager.get_user_config(self.chat_id)
        vol_threshold = user_config.get('VOLUME_THRESHOLD', 1000)
        spread_threshold = user_config.get('SPREAD_THRESHOLD', 0.1)
        natr_threshold = user_config.get('NATR_THRESHOLD', 0.5)

        # Проверяем активность по фильтрам
        is_active = (
            data.get('volume', 0) >= vol_threshold and
            data.get('spread', 0) >= spread_threshold and
            data.get('natr', 0) >= natr_threshold and
            data.get('trades', 0) > 0
        )

        if is_active:
            if symbol not in self.active_coins:
                # Создаем новое уведомление
                await self._create_notification(symbol, data, now)
            else:
                # Обновляем существующее
                await self._update_notification(symbol, data, now)
        else:
            # Завершаем активность если была
            if symbol in self.active_coins:
                coin_info = self.active_coins[symbol]
                if now - coin_info['last_active'] > 60:  # Таймаут неактивности
                    await self._end_activity(symbol, now)

    async def _create_notification(self, symbol: str, data: Dict, now: float):
        """Создает уведомление"""
        message = (
            f"🚨 <b>{symbol}_USDT активен</b>\n"
            f"🔄 Изм: {data['change']:+.2f}%  🔁 Сделок: {data['trades']}\n"
            f"📊 Объём: ${data['volume']:,.2f}  NATR: {data['natr']:.2f}%\n"
            f"⇄ Спред: {data['spread']:.2f}%"
        )

        msg_id = await self.bot._send_personal_message(self.chat_id, message)

        if msg_id:
            self.active_coins[symbol] = {
                'start': now,
                'last_active': now,
                'data': data.copy(),
                'msg_id': msg_id
            }

    async def _update_notification(self, symbol: str, data: Dict, now: float):
        """Обновляет уведомление"""
        coin_info = self.active_coins[symbol]
        coin_info['last_active'] = now
        coin_info['data'] = data

        msg_id = coin_info.get('msg_id')
        if msg_id:
            new_message = (
                f"🚨 <b>{symbol}_USDT активен</b>\n"
                f"🔄 Изм: {data['change']:+.2f}%  🔁 Сделок: {data['trades']}\n"
                f"📊 Объём: ${data['volume']:,.2f}  NATR: {data['natr']:.2f}%\n"
                f"⇄ Спред: {data['spread']:.2f}%"
            )
            await self.bot._edit_personal_message(self.chat_id, msg_id, new_message)

    async def _end_activity(self, symbol: str, end_time: float):
        """Завершает активность"""
        if symbol not in self.active_coins:
            return

        coin_info = self.active_coins[symbol]
        duration = end_time - coin_info['start']

        # Удаляем уведомление
        msg_id = coin_info.get('msg_id')
        if msg_id:
            await self.bot._delete_personal_message(self.chat_id, msg_id)

        # Отправляем итоговое сообщение если активность была >= 60 секунд
        if duration >= 60:
            duration_min = int(duration // 60)
            duration_sec = int(duration % 60)
            end_message = (
                f"✅ <b>{symbol}_USDT завершил активность</b>\n"
                f"⏱ Длительность: {duration_min} мин {duration_sec} сек"
            )
            await self.bot._send_personal_message(self.chat_id, end_message)

        del self.active_coins[symbol]

    def _format_report(self, results: List[Dict], failed_coins: List[str]) -> str:
        """Форматирует отчет"""
        results.sort(key=lambda x: x['volume'], reverse=True)

        parts = ["<b>📊 Ваш мониторинг</b>\n"]

        # Получаем пользовательские фильтры
        user_config = user_manager.get_user_config(self.chat_id)
        vol_thresh = user_config.get('VOLUME_THRESHOLD', 1000)
        spread_thresh = user_config.get('SPREAD_THRESHOLD', 0.1)
        natr_thresh = user_config.get('NATR_THRESHOLD', 0.5)

        parts.append(
            f"<i>Фильтры: 1м оборот ≥${vol_thresh:,}, "
            f"Спред ≥{spread_thresh}%, NATR ≥{natr_thresh}%</i>\n"
        )

        if failed_coins:
            parts.append(f"⚠ <i>Ошибки: {', '.join(failed_coins[:3])}</i>\n")

        active_coins = [r for r in results if r.get('active', False)]
        if active_coins:
            parts.append("<b>🟢 АКТИВНЫЕ:</b>")
            for coin in active_coins[:8]:
                parts.append(
                    f"• <b>{coin['symbol']}</b> "
                    f"${coin['volume']:,.0f} | {coin['change']:+.1f}% | "
                    f"T:{coin['trades']} | S:{coin['spread']:.2f}% | N:{coin['natr']:.2f}%"
                )
            parts.append("")

        inactive_coins = [r for r in results if not r.get('active', False)]
        if inactive_coins:
            parts.append("<b>🔴 НЕАКТИВНЫЕ (топ по объёму):</b>")
            for coin in inactive_coins[:5]:
                parts.append(
                    f"• <b>{coin['symbol']}</b> "
                    f"${coin['volume']:,.0f} | {coin['change']:+.1f}% | "
                    f"T:{coin['trades']} | S:{coin['spread']:.2f}% | N:{coin['natr']:.2f}%"
                )

        parts.append(f"\n📈 Активных: {len(active_coins)}/{len(results)}")

        return "\n".join(parts)

    async def update_monitoring(self):
        """Обновляет мониторинг с новыми настройками"""
        if not self.running:
            return False

        try:
            # Принудительно обновляем отчет мониторинга
            user_watchlist = user_manager.get_user_watchlist(self.chat_id)
            if user_watchlist:

                # Delete the old monitoring message
                if self.monitoring_message_id:
                    await self.bot._delete_personal_message(self.chat_id, self.monitoring_message_id)
                    self.monitoring_message_id = None  # Reset the message ID after deletion

                results, failed_coins = await self.bot._fetch_user_data(user_watchlist, self.chat_id)

                if results:
                    report = self._format_report(results, failed_coins)
                    # Send a new monitoring message
                    self.monitoring_message_id = await self.bot._send_personal_message(self.chat_id, report)



                # Отправляем уведомление об успешном обновлении
                update_text = "✅ <b>Мониторинг обновлен</b>"
                await self.bot._send_personal_message(self.chat_id, update_text)
            else:
                update_text = "⚠️ <b>Нет монет для мониторинга</b>\nДобавьте монеты в свой список"
                await self.bot._send_personal_message(self.chat_id, update_text)

            return True

        except Exception as e:
            bot_logger.error(f"Ошибка обновления мониторинга для {self.chat_id}: {e}")
            return False


class UserModesManager:
    """Менеджер режимов для всех пользователей"""

    def __init__(self, bot_instance):
        self.bot = bot_instance
        self.user_modes: Dict[str, UserBotMode] = {}

    async def start_user_mode(self, chat_id: str) -> bool:
        """Запускает режим для пользователя"""
        chat_id_str = str(chat_id)

        # Проверяем права пользователя
        if not user_manager.is_admin(chat_id) and not user_manager.is_user_approved(chat_id):
            return False

        # Проверяем список монет
        user_watchlist = user_manager.get_user_watchlist(chat_id_str)
        if not user_watchlist:
            return False

        # Останавливаем существующий режим если есть
        if chat_id_str in self.user_modes:
            await self.stop_user_mode(chat_id_str)

        # Создаем новый режим
        user_mode = UserBotMode(chat_id_str, self.bot)
        success = await user_mode.start()

        if success:
            self.user_modes[chat_id_str] = user_mode
            bot_logger.info(f"Режим запущен для пользователя {chat_id_str}")

        return success

    async def stop_user_mode(self, chat_id: str) -> bool:
        """Останавливает режим пользователя"""
        chat_id_str = str(chat_id)

        if chat_id_str not in self.user_modes:
            return False

        user_mode = self.user_modes[chat_id_str]
        success = await user_mode.stop()

        if success:
            del self.user_modes[chat_id_str]
            bot_logger.info(f"Режим остановлен для пользователя {chat_id_str}")

        return success

    def is_user_mode_running(self, chat_id: str) -> bool:
        """Проверяет, запущен ли режим у пользователя"""
        chat_id_str = str(chat_id)
        if chat_id_str in self.user_modes:
            return self.user_modes[chat_id_str].running
        return False

    def get_user_mode_stats(self, chat_id: str) -> Dict:
        """Возвращает статистику режима пользователя"""
        chat_id_str = str(chat_id)
        if chat_id_str in self.user_modes:
            mode = self.user_modes[chat_id_str]
            return {
                'running': mode.running,
                'start_time': mode.start_time,
                'active_coins': len(mode.active_coins),
                'uptime': time.time() - mode.start_time if mode.running else 0
            }
        return {'running': False}

    def get_all_stats(self) -> Dict:
        """Возвращает статистику всех режимов"""
        stats = {
            'total_users': len(self.user_modes),
            'running_modes': sum(1 for mode in self.user_modes.values() if mode.running),
            'users': {}
        }

        for chat_id, mode in self.user_modes.items():
            stats['users'][chat_id] = {
                'running': mode.running,
                'active_coins': len(mode.active_coins),
                'uptime': time.time() - mode.start_time if mode.running else 0
            }

        return stats

    async def stop_all_user_modes(self):
        """Останавливает все режимы"""
        for chat_id in list(self.user_modes.keys()):
            await self.stop_user_mode(chat_id)

    async def update_user_mode(self, chat_id: str) -> bool:
        """Обновляет режим пользователя с новыми настройками"""
        chat_id_str = str(chat_id)

        if chat_id_str not in self.user_modes:
            return False

        user_mode = self.user_modes[chat_id_str]
        if not user_mode.running:
            return False

        return await user_mode.update_monitoring()


# Глобальный экземпляр менеджера (будет инициализирован в telegram_bot.py)
user_modes_manager = None

"""
Упрощенный менеджер режимов бота для каждого пользователя
Каждый пользователь независимо запускает/останавливает свой мониторинг
"""

import asyncio
import time
from typing import Dict, Optional, Any, List
from logger import bot_logger
from user_manager import user_manager


class UserBotMode:
    """Режим бота для конкретного пользователя"""

    def __init__(self, chat_id: str, bot_instance):
        self.chat_id = chat_id
        self.bot = bot_instance
        self.running = False
        self.start_time = 0
        self.active_coins = {}
        self.monitoring_message_id = None
        self.task = None

    async def start(self) -> bool:
        """Запускает режим мониторинга для пользователя"""
        if self.running:
            return False

        user_watchlist = user_manager.get_user_watchlist(self.chat_id)
        if not user_watchlist:
            return False

        self.running = True
        self.start_time = time.time()
        self.active_coins.clear()

        # Отправляем начальное сообщение
        initial_text = f"🔄 <b>Ваш мониторинг запущен</b>\nОтслеживается {len(user_watchlist)} монет"
        self.monitoring_message_id = await self.bot._send_personal_message(self.chat_id, initial_text)

        # Запускаем цикл мониторинга
        self.task = asyncio.create_task(self._monitoring_loop())

        bot_logger.info(f"Мониторинг запущен для пользователя {self.chat_id}")
        return True

    async def stop(self) -> bool:
        """Останавливает режим мониторинга"""
        if not self.running:
            return False

        self.running = False

        # Останавливаем задачу
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

        # Удаляем активные уведомления
        for coin_data in list(self.active_coins.values()):
            msg_id = coin_data.get('msg_id')
            if msg_id:
                await self.bot._delete_personal_message(self.chat_id, msg_id)

        # Удаляем сообщение мониторинга
        if self.monitoring_message_id:
            await self.bot._delete_personal_message(self.chat_id, self.monitoring_message_id)

        # Отправляем уведомление об остановке
        stop_text = "🛑 <b>Ваш мониторинг остановлен</b>"
        await self.bot._send_personal_message(self.chat_id, stop_text)

        self.active_coins.clear()
        self.monitoring_message_id = None

        bot_logger.info(f"Мониторинг остановлен для пользователя {self.chat_id}")
        return True

    async def _monitoring_loop(self):
        """Цикл мониторинга для пользователя"""
        cycle_count = 0

        while self.running:
            try:
                cycle_count += 1

                # Получаем список монет пользователя
                user_watchlist = user_manager.get_user_watchlist(self.chat_id)
                if not user_watchlist:
                    await asyncio.sleep(5)
                    continue

                # Получаем данные монет
                results, failed_coins = await self.bot._fetch_user_data(user_watchlist, self.chat_id)

                # Обрабатываем уведомления
                for coin_data in results:
                    if not self.running:
                        break

                    symbol = coin_data['symbol']
                    await self._process_notification(symbol, coin_data)

                # Обновляем отчет мониторинга
                if results:
                    report = self._format_report(results, failed_coins)
                    if self.monitoring_message_id:
                        self.monitoring_message_id = await self.bot._edit_personal_message(self.chat_id, self.monitoring_message_id, report)

                await asyncio.sleep(10)  # Интервал обновления

            except asyncio.CancelledError:
                break
            except Exception as e:
                bot_logger.error(f"Ошибка в цикле мониторинга {self.chat_id}: {e}")
                await asyncio.sleep(2)

    async def _process_notification(self, symbol: str, data: Dict):
        """Обработка уведомлений"""
        now = time.time()

        # Получаем пользовательские фильтры
        user_config = user_manager.get_user_config(self.chat_id)
        vol_threshold = user_config.get('VOLUME_THRESHOLD', 1000)
        spread_threshold = user_config.get('SPREAD_THRESHOLD', 0.1)
        natr_threshold = user_config.get('NATR_THRESHOLD', 0.5)

        # Проверяем активность по фильтрам
        is_active = (
            data.get('volume', 0) >= vol_threshold and
            data.get('spread', 0) >= spread_threshold and
            data.get('natr', 0) >= natr_threshold and
            data.get('trades', 0) > 0
        )

        if is_active:
            if symbol not in self.active_coins:
                # Создаем новое уведомление
                await self._create_notification(symbol, data, now)
            else:
                # Обновляем существующее
                await self._update_notification(symbol, data, now)
        else:
            # Завершаем активность если была
            if symbol in self.active_coins:
                coin_info = self.active_coins[symbol]
                if now - coin_info['last_active'] > 60:  # Таймаут неактивности
                    await self._end_activity(symbol, now)

    async def _create_notification(self, symbol: str, data: Dict, now: float):
        """Создает уведомление"""
        message = (
            f"🚨 <b>{symbol}_USDT активен</b>\n"
            f"🔄 Изм: {data['change']:+.2f}%  🔁 Сделок: {data['trades']}\n"
            f"📊 Объём: ${data['volume']:,.2f}  NATR: {data['natr']:.2f}%\n"
            f"⇄ Спред: {data['spread']:.2f}%"
        )

        msg_id = await self.bot._send_personal_message(self.chat_id, message)

        if msg_id:
            self.active_coins[symbol] = {
                'start': now,
                'last_active': now,
                'data': data.copy(),
                'msg_id': msg_id
            }

    async def _update_notification(self, symbol: str, data: Dict, now: float):
        """Обновляет уведомление"""
        coin_info = self.active_coins[symbol]
        coin_info['last_active'] = now
        coin_info['data'] = data

        msg_id = coin_info.get('msg_id')
        if msg_id:
            new_message = (
                f"🚨 <b>{symbol}_USDT активен</b>\n"
                f"🔄 Изм: {data['change']:+.2f}%  🔁 Сделок: {data['trades']}\n"
                f"📊 Объём: ${data['volume']:,.2f}  NATR: {data['natr']:.2f}%\n"
                f"⇄ Спред: {data['spread']:.2f}%"
            )
            await self.bot._edit_personal_message(self.chat_id, msg_id, new_message)

    async def _end_activity(self, symbol: str, end_time: float):
        """Завершает активность"""
        if symbol not in self.active_coins:
            return

        coin_info = self.active_coins[symbol]
        duration = end_time - coin_info['start']

        # Удаляем уведомление
        msg_id = coin_info.get('msg_id')
        if msg_id:
            await self.bot._delete_personal_message(self.chat_id, msg_id)

        # Отправляем итоговое сообщение если активность была >= 60 секунд
        if duration >= 60:
            duration_min = int(duration // 60)
            duration_sec = int(duration % 60)
            end_message = (
                f"✅ <b>{symbol}_USDT завершил активность</b>\n"
                f"⏱ Длительность: {duration_min} мин {duration_sec} сек"
            )
            await self.bot._send_personal_message(self.chat_id, end_message)

        del self.active_coins[symbol]

    def _format_report(self, results: List[Dict], failed_coins: List[str]) -> str:
        """Форматирует отчет"""
        results.sort(key=lambda x: x['volume'], reverse=True)

        parts = ["<b>📊 Ваш мониторинг</b>\n"]

        # Получаем пользовательские фильтры
        user_config = user_manager.get_user_config(self.chat_id)
        vol_thresh = user_config.get('VOLUME_THRESHOLD', 1000)
        spread_thresh = user_config.get('SPREAD_THRESHOLD', 0.1)
        natr_thresh = user_config.get('NATR_THRESHOLD', 0.5)

        parts.append(
            f"<i>Фильтры: 1м оборот ≥${vol_thresh:,}, "
            f"Спред ≥{spread_thresh}%, NATR ≥{natr_thresh}%</i>\n"
        )

        if failed_coins:
            parts.append(f"⚠ <i>Ошибки: {', '.join(failed_coins[:3])}</i>\n")

        active_coins = [r for r in results if r.get('active', False)]
        if active_coins:
            parts.append("<b>🟢 АКТИВНЫЕ:</b>")
            for coin in active_coins[:8]:
                parts.append(
                    f"• <b>{coin['symbol']}</b> "
                    f"${coin['volume']:,.0f} | {coin['change']:+.1f}% | "
                    f"T:{coin['trades']} | S:{coin['spread']:.2f}% | N:{coin['natr']:.2f}%"
                )
            parts.append("")

        inactive_coins = [r for r in results if not r.get('active', False)]
        if inactive_coins:
            parts.append("<b>🔴 НЕАКТИВНЫЕ (топ по объёму):</b>")
            for coin in inactive_coins[:5]:
                parts.append(
                    f"• <b>{coin['symbol']}</b> "
                    f"${coin['volume']:,.0f} | {coin['change']:+.1f}% | "
                    f"T:{coin['trades']} | S:{coin['spread']:.2f}% | N:{coin['natr']:.2f}%"
                )

        parts.append(f"\n📈 Активных: {len(active_coins)}/{len(results)}")

        return "\n".join(parts)

    async def update_monitoring(self):
        """Обновляет мониторинг с новыми настройками"""
        if not self.running:
            return False

        try:
            # Delete the old monitoring message
            if self.monitoring_message_id:
                await self.bot._delete_personal_message(self.chat_id, self.monitoring_message_id)
                self.monitoring_message_id = None

            # Принудительно обновляем отчет мониторинга
            user_watchlist = user_manager.get_user_watchlist(self.chat_id)
            if user_watchlist:
                results, failed_coins = await self.bot._fetch_user_data(user_watchlist, self.chat_id)

                if results:
                    report = self._format_report(results, failed_coins)
                    # Отправляем отчет мониторинга
                    self.monitoring_message_id = await self.bot._send_personal_message(self.chat_id, report)

                # Отправляем уведомление об успешном обновлении
                update_text = "✅ <b>Мониторинг обновлен</b>"
                await self.bot._send_personal_message(self.chat_id, update_text)
            else:
                update_text = "⚠️ <b>Нет монет для мониторинга</b>\nДобавьте монеты в свой список"
                await self.bot._send_personal_message(self.chat_id, update_text)

            return True

        except Exception as e:
            bot_logger.error(f"Ошибка обновления мониторинга для {self.chat_id}: {e}")
            return False


class UserModesManager:
    """Менеджер режимов для всех пользователей"""

    def __init__(self, bot_instance):
        self.bot = bot_instance
        self.user_modes: Dict[str, UserBotMode] = {}

    async def start_user_mode(self, chat_id: str) -> bool:
        """Запускает режим для пользователя"""
        chat_id_str = str(chat_id)

        # Проверяем права пользователя
        if not user_manager.is_admin(chat_id) and not user_manager.is_user_approved(chat_id):
            return False

        # Проверяем список монет
        user_watchlist = user_manager.get_user_watchlist(chat_id_str)
        if not user_watchlist:
            return False

        # Останавливаем существующий режим если есть
        if chat_id_str in self.user_modes:
            await self.stop_user_mode(chat_id_str)

        # Создаем новый режим
        user_mode = UserBotMode(chat_id_str, self.bot)
        success = await user_mode.start()

        if success:
            self.user_modes[chat_id_str] = user_mode
            bot_logger.info(f"Режим запущен для пользователя {chat_id_str}")

        return success

    async def stop_user_mode(self, chat_id: str) -> bool:
        """Останавливает режим пользователя"""
        chat_id_str = str(chat_id)

        if chat_id_str not in self.user_modes:
            return False

        user_mode = self.user_modes[chat_id_str]
        success = await user_mode.stop()

        if success:
            del self.user_modes[chat_id_str]
            bot_logger.info(f"Режим остановлен для пользователя {chat_id_str}")

        return success

    def is_user_mode_running(self, chat_id: str) -> bool:
        """Проверяет, запущен ли режим у пользователя"""
        chat_id_str = str(chat_id)
        if chat_id_str in self.user_modes:
            return self.user_modes[chat_id_str].running
        return False

    def get_user_mode_stats(self, chat_id: str) -> Dict:
        """Возвращает статистику режима пользователя"""
        chat_id_str = str(chat_id)
        if chat_id_str in self.user_modes:
            mode = self.user_modes[chat_id_str]
            return {
                'running': mode.running,
                'start_time': mode.start_time,
                'active_coins': len(mode.active_coins),
                'uptime': time.time() - mode.start_time if mode.running else 0
            }
        return {'running': False}

    def get_all_stats(self) -> Dict:
        """Возвращает статистику всех режимов"""
        stats = {
            'total_users': len(self.user_modes),
            'running_modes': sum(1 for mode in self.user_modes.values() if mode.running),
            'users': {}
        }

        for chat_id, mode in self.user_modes.items():
            stats['users'][chat_id] = {'running': mode.running,
                'active_coins': len(mode.active_coins),
                'uptime': time.time() - mode.start_time if mode.running else 0
            }

        return stats

    async def stop_all_user_modes(self):
        """Останавливает все режимы"""
        for chat_id in list(self.user_modes.keys()):
            await self.stop_user_mode(chat_id)

    async def update_user_mode(self, chat_id: str) -> bool:
        """Обновляет режим пользователя с новыми настройками"""
        chat_id_str = str(chat_id)

        if chat_id_str not in self.user_modes:
            return False

        user_mode = self.user_modes[chat_id_str]
        if not user_mode.running:
            return False

        return await user_mode.update_monitoring()


# Глобальный экземпляр менеджера (будет инициализирован в telegram_bot.py)
user_modes_manager = None