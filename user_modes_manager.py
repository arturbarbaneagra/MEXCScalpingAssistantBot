
"""
Менеджер персональных режимов пользователей
Каждый пользователь может независимо управлять своими режимами работы
"""

import asyncio
import time
from typing import Dict, Optional, Any
from logger import bot_logger
from user_manager import user_manager


class UserMode:
    """Базовый класс для пользовательского режима"""
    def __init__(self, user_id: str, bot_instance):
        self.user_id = user_id
        self.bot = bot_instance
        self.running = False
        self.task = None
        
    async def start(self):
        """Запуск режима"""
        if self.running:
            return False
        self.running = True
        return True
        
    async def stop(self):
        """Остановка режима"""
        if not self.running:
            return False
            
        self.running = False
        
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await asyncio.wait_for(self.task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
                
        self.task = None
        return True
        
    def get_stats(self):
        """Возвращает статистику режима"""
        return {
            'user_id': self.user_id,
            'running': self.running,
            'mode_type': self.__class__.__name__
        }


class UserNotificationMode(UserMode):
    """Персональный режим уведомлений для пользователя"""
    
    def __init__(self, user_id: str, bot_instance):
        super().__init__(user_id, bot_instance)
        self.active_coins = {}
        
    async def start(self):
        if not await super().start():
            return False
            
        bot_logger.info(f"🔔 Запуск режима уведомлений для пользователя {self.user_id}")
        
        # Сообщение будет отправлено из основного обработчика, чтобы избежать дублирования
        
        # Запускаем основной цикл
        self.task = asyncio.create_task(self._notification_loop())
        return True
        
    async def _notification_loop(self):
        """Основной цикл уведомлений"""
        from api_client import api_client
        from config import config_manager
        
        while self.running:
            try:
                # Получаем список монет в зависимости от роли
                if user_manager.is_admin(self.user_id):
                    # Для админа используем глобальный список
                    from watchlist_manager import watchlist_manager
                    user_watchlist = list(watchlist_manager.get_all())
                else:
                    # Для обычного пользователя используем его личный список
                    user_watchlist = user_manager.get_user_watchlist(self.user_id)
                
                if not user_watchlist:
                    await asyncio.sleep(30)
                    continue
                
                # Получаем настройки пользователя
                if user_manager.is_admin(self.user_id):
                    # Для админа используем глобальные настройки
                    from config import config_manager
                    user_config = {
                        'VOLUME_THRESHOLD': config_manager.get('VOLUME_THRESHOLD'),
                        'SPREAD_THRESHOLD': config_manager.get('SPREAD_THRESHOLD'),
                        'NATR_THRESHOLD': config_manager.get('NATR_THRESHOLD')
                    }
                else:
                    user_config = user_manager.get_user_config(self.user_id)
                
                # Проверяем каждую монету
                for symbol in user_watchlist:
                    if not self.running:
                        break
                        
                    try:
                        # Получаем данные монеты
                        ticker_data = await api_client.get_ticker_data(symbol)
                        if not ticker_data:
                            continue
                        
                        # Преобразуем данные
                        volume = float(ticker_data.get('quoteVolume', 0))
                        price = float(ticker_data.get('lastPrice', 0))
                        
                        # Рассчитываем спред и NATR
                        try:
                            from data_validator import data_validator
                            spread = data_validator.calculate_spread(ticker_data)
                            natr = await data_validator.calculate_natr(symbol)
                        except Exception as e:
                            bot_logger.debug(f"Ошибка расчета индикаторов для {symbol}: {e}")
                            spread = 0.0
                            natr = 0.0
                        
                        # Создаем объект coin_data для совместимости
                        coin_data = {
                            'symbol': symbol,
                            'price': price,
                            'volume': volume,
                            'spread': spread,
                            'natr': natr
                        }
                        
                        is_active = (
                            volume >= user_config.get('VOLUME_THRESHOLD', 1000) and
                            spread >= user_config.get('SPREAD_THRESHOLD', 0.1) and
                            natr >= user_config.get('NATR_THRESHOLD', 0.5)
                        )
                        
                        if is_active and symbol not in self.active_coins:
                            # Новая активная монета
                            self.active_coins[symbol] = {
                                'start_time': time.time(),
                                'last_active': time.time(),
                                'initial_price': coin_data.get('price', 0)
                            }
                            
                            # Отправляем уведомление
                            await self._send_activation_alert(symbol, coin_data)
                            
                        elif is_active and symbol in self.active_coins:
                            # Обновляем время последней активности
                            self.active_coins[symbol]['last_active'] = time.time()
                            
                        elif not is_active and symbol in self.active_coins:
                            # Монета стала неактивной
                            await self._send_deactivation_alert(symbol, coin_data)
                            del self.active_coins[symbol]
                            
                    except Exception as e:
                        bot_logger.debug(f"Ошибка проверки {symbol} для пользователя {self.user_id}: {e}")
                        
                await asyncio.sleep(10)  # Проверяем каждые 10 секунд
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                bot_logger.error(f"Ошибка в цикле уведомлений пользователя {self.user_id}: {e}")
                await asyncio.sleep(5)
                
    async def _send_activation_alert(self, symbol: str, coin_data: Dict):
        """Отправляет уведомление об активации монеты"""
        try:
            alert_text = (
                f"🔥 <b>{symbol} стала активной!</b>\n\n"
                f"💰 Цена: <code>${coin_data.get('price', 0):.6f}</code>\n"
                f"📊 Объём: <code>${coin_data.get('volume', 0):,.0f}</code>\n"
                f"⇄ Спред: <code>{coin_data.get('spread', 0):.2f}%</code>\n"
                f"📈 NATR: <code>{coin_data.get('natr', 0):.2f}%</code>"
            )
            
            await self.bot.app.bot.send_message(
                chat_id=self.user_id,
                text=alert_text,
                parse_mode="HTML"
            )
            
        except Exception as e:
            bot_logger.error(f"Ошибка отправки уведомления пользователю {self.user_id}: {e}")
            
    async def _send_deactivation_alert(self, symbol: str, coin_data: Dict):
        """Отправляет уведомление о деактивации монеты"""
        try:
            duration = time.time() - self.active_coins[symbol]['start_time']
            alert_text = (
                f"⏹️ <b>{symbol} больше не активна</b>\n\n"
                f"💰 Цена: <code>${coin_data.get('price', 0):.6f}</code>\n"
                f"⏱️ Была активна: <code>{duration/60:.1f} минут</code>"
            )
            
            await self.bot.app.bot.send_message(
                chat_id=self.user_id,
                text=alert_text,
                parse_mode="HTML"
            )
            
        except Exception as e:
            bot_logger.error(f"Ошибка отправки уведомления пользователю {self.user_id}: {e}")
            
    def get_stats(self):
        stats = super().get_stats()
        stats.update({
            'active_coins_count': len(self.active_coins),
            'active_coins': list(self.active_coins.keys())
        })
        return stats


class UserMonitoringMode(UserMode):
    """Персональный режим мониторинга для пользователя"""
    
    def __init__(self, user_id: str, bot_instance):
        super().__init__(user_id, bot_instance)
        self.monitoring_message_id = None
        
    async def start(self):
        if not await super().start():
            return False
            
        bot_logger.info(f"📊 Запуск режима мониторинга для пользователя {self.user_id}")
        
        # Отправляем начальное сообщение
        try:
            message = await self.bot.app.bot.send_message(
                chat_id=self.user_id,
                text="🔄 <b>Инициализация мониторинга...</b>",
                parse_mode="HTML"
            )
            self.monitoring_message_id = message.message_id
        except Exception as e:
            bot_logger.error(f"Ошибка отправки сообщения пользователю {self.user_id}: {e}")
            
        # Запускаем основной цикл
        self.task = asyncio.create_task(self._monitoring_loop())
        
        # Сообщение будет отправлено из основного обработчика, чтобы избежать дублирования
            
        return True
        
    async def stop(self):
        if not await super().stop():
            return False
            
        # Удаляем сообщение мониторинга
        if self.monitoring_message_id:
            try:
                await self.bot.app.bot.delete_message(
                    chat_id=self.user_id,
                    message_id=self.monitoring_message_id
                )
            except Exception as e:
                bot_logger.debug(f"Ошибка удаления сообщения мониторинга: {e}")
                
        self.monitoring_message_id = None
        return True
        
    async def _monitoring_loop(self):
        """Основной цикл мониторинга"""
        from api_client import api_client
        
        while self.running:
            try:
                # Получаем список монет в зависимости от роли
                if user_manager.is_admin(self.user_id):
                    # Для админа используем глобальный список
                    from watchlist_manager import watchlist_manager
                    user_watchlist = list(watchlist_manager.get_all())
                    empty_message = "❌ <b>Глобальный список отслеживания пуст</b>\nДобавьте монеты для мониторинга."
                else:
                    # Для обычного пользователя используем его личный список
                    user_watchlist = user_manager.get_user_watchlist(self.user_id)
                    empty_message = "❌ <b>Ваш список отслеживания пуст</b>\nДобавьте монеты для мониторинга."
                
                if not user_watchlist:
                    if self.monitoring_message_id:
                        await self._edit_monitoring_message(empty_message)
                    await asyncio.sleep(30)
                    continue
                
                # Получаем данные монет
                results = []
                failed_coins = []
                
                for symbol in user_watchlist:
                    if not self.running:
                        break
                        
                    try:
                        # Получаем базовые данные монеты
                        ticker_data = await api_client.get_ticker_data(symbol)
                        if ticker_data:
                            # Преобразуем в формат для отчета
                            coin_data = {
                                'symbol': symbol,
                                'price': float(ticker_data.get('lastPrice', 0)),
                                'volume': float(ticker_data.get('quoteVolume', 0)),
                                'change': float(ticker_data.get('priceChangePercent', 0)),
                                'spread': 0.0,  # Будет рассчитан отдельно
                                'natr': 0.0     # Будет рассчитан отдельно
                            }
                            
                            # Рассчитываем спред
                            try:
                                from data_validator import data_validator
                                coin_data['spread'] = data_validator.calculate_spread(ticker_data)
                            except Exception:
                                pass
                            
                            # Рассчитываем NATR
                            try:
                                from data_validator import data_validator
                                coin_data['natr'] = await data_validator.calculate_natr(symbol)
                            except Exception:
                                pass
                            
                            results.append(coin_data)
                        else:
                            failed_coins.append(symbol)
                    except Exception as e:
                        bot_logger.debug(f"Ошибка получения данных {symbol}: {e}")
                        failed_coins.append(symbol)
                        
                # Обновляем отчет
                if results or failed_coins:
                    report = self._format_monitoring_report(results, failed_coins)
                    if self.monitoring_message_id:
                        await self._edit_monitoring_message(report)
                        
                await asyncio.sleep(15)  # Обновляем каждые 15 секунд
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                bot_logger.error(f"Ошибка в цикле мониторинга пользователя {self.user_id}: {e}")
                await asyncio.sleep(5)
                
    async def _edit_monitoring_message(self, text: str):
        """Редактирует сообщение мониторинга"""
        try:
            await self.bot.app.bot.edit_message_text(
                chat_id=self.user_id,
                message_id=self.monitoring_message_id,
                text=text,
                parse_mode="HTML"
            )
        except Exception as e:
            if "message is not modified" not in str(e).lower():
                bot_logger.debug(f"Ошибка редактирования сообщения мониторинга: {e}")
                
    def _format_monitoring_report(self, results: list, failed_coins: list) -> str:
        """Форматирует отчет мониторинга"""
        # Получаем настройки в зависимости от роли
        if user_manager.is_admin(self.user_id):
            # Для админа используем глобальные настройки
            from config import config_manager
            vol_thresh = config_manager.get('VOLUME_THRESHOLD')
            spread_thresh = config_manager.get('SPREAD_THRESHOLD')
            natr_thresh = config_manager.get('NATR_THRESHOLD')
            title = "<b>📊 Администраторский мониторинг</b>\n"
            filters_text = f"<i>Глобальные фильтры: Объём ≥${vol_thresh:,}, Спред ≥{spread_thresh}%, NATR ≥{natr_thresh}%</i>\n"
        else:
            # Для обычного пользователя используем его настройки
            user_config = user_manager.get_user_config(self.user_id)
            vol_thresh = user_config.get('VOLUME_THRESHOLD', 1000)
            spread_thresh = user_config.get('SPREAD_THRESHOLD', 0.1)
            natr_thresh = user_config.get('NATR_THRESHOLD', 0.5)
            title = "<b>📊 Ваш персональный мониторинг</b>\n"
            filters_text = f"<i>Ваши фильтры: Объём ≥${vol_thresh:,}, Спред ≥{spread_thresh}%, NATR ≥{natr_thresh}%</i>\n"
        
        results.sort(key=lambda x: x.get('volume', 0), reverse=True)
        
        parts = [
            title,
            filters_text
        ]
        
        if failed_coins:
            parts.append(f"⚠ <i>Ошибки: {', '.join(failed_coins[:3])}</i>\n")
            
        # Активные монеты
        active_coins = []
        inactive_coins = []
        
        for coin in results:
            volume = coin.get('volume', 0)
            spread = coin.get('spread', 0)
            natr = coin.get('natr', 0)
            
            is_active = (
                volume >= vol_thresh and
                spread >= spread_thresh and
                natr >= natr_thresh
            )
            
            if is_active:
                active_coins.append(coin)
            else:
                inactive_coins.append(coin)
                
        if active_coins:
            parts.append("<b>🟢 АКТИВНЫЕ:</b>")
            for coin in active_coins[:8]:
                parts.append(
                    f"• <b>{coin['symbol']}</b> "
                    f"${coin.get('volume', 0):,.0f} | {coin.get('change', 0):+.1f}% | "
                    f"S:{coin.get('spread', 0):.2f}% | N:{coin.get('natr', 0):.2f}%"
                )
            parts.append("")
            
        if inactive_coins:
            parts.append("<b>🔴 НЕАКТИВНЫЕ:</b>")
            for coin in inactive_coins[:6]:
                parts.append(
                    f"• <b>{coin['symbol']}</b> "
                    f"${coin.get('volume', 0):,.0f} | {coin.get('change', 0):+.1f}% | "
                    f"S:{coin.get('spread', 0):.2f}% | N:{coin.get('natr', 0):.2f}%"
                )
                
        parts.append(f"\n📈 Активных: {len(active_coins)}/{len(results)}")
        
        report = "\n".join(parts)
        if len(report) > 4000:
            report = report[:4000] + "\n... <i>(отчет обрезан)</i>"
            
        return report
        
    def get_stats(self):
        stats = super().get_stats()
        
        # Получаем размер списка в зависимости от роли
        if user_manager.is_admin(self.user_id):
            from watchlist_manager import watchlist_manager
            watchlist_size = watchlist_manager.size()
        else:
            user_watchlist = user_manager.get_user_watchlist(self.user_id)
            watchlist_size = len(user_watchlist) if user_watchlist else 0
            
        stats.update({
            'monitoring_message_id': self.monitoring_message_id,
            'watchlist_size': watchlist_size
        })
        return stats


class UserModesManager:
    """Менеджер персональных режимов пользователей"""
    
    def __init__(self, bot_instance):
        self.bot = bot_instance
        self.user_modes: Dict[str, Dict[str, UserMode]] = {}
        
    async def start_user_mode(self, user_id: str, mode_type: str) -> bool:
        """Запускает режим для пользователя"""
        user_id_str = str(user_id)
        
        # Останавливаем текущий режим если есть
        await self.stop_user_mode(user_id_str)
        
        # Создаем новый режим
        if user_id_str not in self.user_modes:
            self.user_modes[user_id_str] = {}
            
        if mode_type == 'notification':
            mode = UserNotificationMode(user_id_str, self.bot)
        elif mode_type == 'monitoring':
            mode = UserMonitoringMode(user_id_str, self.bot)
        else:
            return False
            
        # Запускаем режим
        success = await mode.start()
        if success:
            self.user_modes[user_id_str][mode_type] = mode
            
            # Сохраняем состояние в данных пользователя
            user_manager.update_user_data(user_id_str, {
                'current_mode': mode_type,
                'mode_start_time': time.time()
            })
            
            bot_logger.info(f"Режим {mode_type} запущен для пользователя {user_id_str}")
            
        return success
        
    async def stop_user_mode(self, user_id: str) -> bool:
        """Останавливает текущий режим пользователя"""
        user_id_str = str(user_id)
        
        if user_id_str not in self.user_modes:
            return False
            
        stopped_any = False
        
        # Останавливаем все режимы пользователя
        for mode_type, mode in list(self.user_modes[user_id_str].items()):
            try:
                await mode.stop()
                del self.user_modes[user_id_str][mode_type]
                stopped_any = True
                bot_logger.info(f"Режим {mode_type} остановлен для пользователя {user_id_str}")
            except Exception as e:
                bot_logger.error(f"Ошибка остановки режима {mode_type} для пользователя {user_id_str}: {e}")
                
        # Очищаем данные если все режимы остановлены
        if not self.user_modes[user_id_str]:
            del self.user_modes[user_id_str]
            
        # Обновляем состояние пользователя
        if stopped_any:
            user_manager.update_user_data(user_id_str, {
                'current_mode': None,
                'mode_stop_time': time.time()
            })
            
        return stopped_any
        
    def get_user_mode(self, user_id: str) -> Optional[str]:
        """Возвращает текущий режим пользователя"""
        user_id_str = str(user_id)
        
        if user_id_str in self.user_modes:
            for mode_type in self.user_modes[user_id_str]:
                return mode_type
                
        return None
        
    def is_user_mode_running(self, user_id: str, mode_type: str = None) -> bool:
        """Проверяет, запущен ли режим у пользователя"""
        user_id_str = str(user_id)
        
        if user_id_str not in self.user_modes:
            return False
            
        if mode_type:
            return mode_type in self.user_modes[user_id_str]
        else:
            return len(self.user_modes[user_id_str]) > 0
            
    def get_user_stats(self, user_id: str) -> Dict:
        """Возвращает статистику режимов пользователя"""
        user_id_str = str(user_id)
        
        if user_id_str not in self.user_modes:
            return {'user_id': user_id_str, 'modes': {}}
            
        stats = {'user_id': user_id_str, 'modes': {}}
        
        for mode_type, mode in self.user_modes[user_id_str].items():
            stats['modes'][mode_type] = mode.get_stats()
            
        return stats
        
    def get_all_stats(self) -> Dict:
        """Возвращает статистику всех пользователей"""
        return {
            'total_users': len(self.user_modes),
            'users': {user_id: self.get_user_stats(user_id) for user_id in self.user_modes}
        }
        
    async def stop_all_modes(self):
        """Останавливает все режимы всех пользователей"""
        for user_id in list(self.user_modes.keys()):
            await self.stop_user_mode(user_id)


# Глобальный экземпляр менеджера (будет инициализирован в telegram_bot.py)
user_modes_manager = None
