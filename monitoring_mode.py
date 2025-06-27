
"""
Модуль режима мониторинга
Отвечает за постоянный мониторинг и отображение сводки
"""

import asyncio
import time
from typing import List, Dict, Optional
from logger import bot_logger
from config import config_manager
from api_client import api_client
from watchlist_manager import watchlist_manager
from session_recorder import session_recorder


class MonitoringMode:
    def __init__(self, telegram_bot):
        self.bot = telegram_bot
        self.running = False
        self.monitoring_message_id: Optional[int] = None
        self.task = None

    async def start(self):
        """Запуск режима мониторинга"""
        if self.running:
            bot_logger.warning("Режим мониторинга уже запущен")
            return

        self.running = True
        self.monitoring_message_id = None

        bot_logger.info("📊 Запуск режима мониторинга")
        
        # Отправляем начальное сообщение
        initial_text = "🔄 <b>Инициализация мониторинга...</b>"
        self.monitoring_message_id = await self.bot.send_message(initial_text)

        # Запускаем основной цикл
        self.task = asyncio.create_task(self._monitoring_loop())

        await self.bot.send_message(
            "✅ <b>Режим мониторинга активирован</b>\n"
            "🚀 <i>Оптимизирован для скальпинга</i>\n"
            "Сводка будет обновляться автоматически."
        )

    async def stop(self):
        """Остановка режима мониторинга"""
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
            except Exception as e:
                bot_logger.debug(f"Ошибка при остановке задачи мониторинга: {e}")

        # Очищаем все pending корутины
        try:
            pending_tasks = [task for task in asyncio.all_tasks() 
                           if not task.done() and 'get_trades_last_minute' in str(task)]
            if pending_tasks:
                for task in pending_tasks:
                    task.cancel()
                await asyncio.gather(*pending_tasks, return_exceptions=True)
        except Exception as e:
            bot_logger.debug(f"Ошибка очистки pending задач: {e}")

        # Удаляем сообщение мониторинга
        if self.monitoring_message_id:
            await self.bot.delete_message(self.monitoring_message_id)
            bot_logger.info("📝 Сообщение мониторинга удалено")

        # Очищаем состояние
        self.monitoring_message_id = None
        self.task = None

    def _chunks(self, lst: List, size: int):
        """Разбивает список на чанки"""
        for i in range(0, len(lst), size):
            yield lst[i:i + size]

    async def _monitoring_loop(self):
        """Основной цикл режима мониторинга"""
        cycle_count = 0

        while self.running:
            try:
                cycle_count += 1

                # Проверяем список отслеживания
                watchlist = watchlist_manager.get_all()
                if not watchlist:
                    no_coins_text = "❌ <b>Список отслеживания пуст</b>\nДобавьте монеты для мониторинга."
                    if self.monitoring_message_id:
                        await self.bot.edit_message(self.monitoring_message_id, no_coins_text)
                    await asyncio.sleep(config_manager.get('MONITORING_UPDATE_INTERVAL'))
                    continue

                # Получаем данные монет
                results, failed_coins = await self._fetch_monitoring_data()

                # Записываем данные активных монет в сессии
                for coin_data in results:
                    if coin_data.get('active'):
                        session_recorder.update_coin_activity(coin_data['symbol'], coin_data)

                # Обновляем отчет
                if results:
                    report = self._format_monitoring_report(results, failed_coins)
                    if self.monitoring_message_id:
                        await self.bot.edit_message(self.monitoring_message_id, report)
                    else:
                        self.monitoring_message_id = await self.bot.send_message(report)

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
                bot_logger.error(f"Ошибка в цикле мониторинга: {e}")
                await asyncio.sleep(1.0)

    async def _fetch_monitoring_data(self):
        """Получает данные для мониторинга"""
        watchlist = list(watchlist_manager.get_all())
        results = []
        failed_coins = []

        batch_size = config_manager.get('CHECK_BATCH_SIZE', 15)
        for batch in self._chunks(watchlist, batch_size):
            if not self.running:
                break

            try:
                batch_data = await api_client.get_batch_coin_data(batch)
                for symbol, coin_data in batch_data.items():
                    if coin_data:
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

    def _format_monitoring_report(self, results: List[Dict], failed_coins: List[str]) -> str:
        """Форматирует отчет мониторинга"""
        results.sort(key=lambda x: x['volume'], reverse=True)

        parts = ["<b>📊 Скальпинг мониторинг (1м данные)</b>\n"]

        vol_thresh = config_manager.get('VOLUME_THRESHOLD')
        spread_thresh = config_manager.get('SPREAD_THRESHOLD')
        natr_thresh = config_manager.get('NATR_THRESHOLD')

        # Проверяем статус Circuit Breakers
        api_status = "🟢"
        try:
            from circuit_breaker import api_circuit_breakers
            open_breakers = [name for name, cb in api_circuit_breakers.items() if cb.state == 'OPEN']
            if open_breakers:
                api_status = f"🟡 API проблемы: {', '.join([name.replace('_api', '') for name in open_breakers[:2]])}"
        except:
            pass

        parts.append(f"<i>API: {api_status}</i>")
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

    def get_stats(self):
        """Возвращает статистику режима"""
        return {
            'active': self.running,
            'monitoring_message_id': self.monitoring_message_id,
            'watchlist_size': watchlist_manager.size()
        }
