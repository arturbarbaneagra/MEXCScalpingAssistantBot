
"""
Автономный монитор активности монет
Работает независимо от выбранного режима бота
"""

import asyncio
import time
from typing import Dict, Set
from logger import bot_logger
from config import config_manager
from api_client import api_client
from watchlist_manager import watchlist_manager
from session_recorder import session_recorder


class AutonomousActivityMonitor:
    def __init__(self):
        self.running = False
        self.monitoring_task = None
        self.tracked_coins: Dict[str, Dict] = {}  # Собственное отслеживание активности
        
    async def start(self):
        """Запуск автономного мониторинга"""
        if self.running:
            bot_logger.warning("Автономный монитор уже запущен")
            return
            
        self.running = True
        self.tracked_coins.clear()
        
        # Запускаем мониторинг в фоне
        self.monitoring_task = asyncio.create_task(self._monitoring_loop())
        
        bot_logger.info("🔍 Автономный монитор активности запущен")
        
    async def stop(self):
        """Остановка автономного мониторинга"""
        if not self.running:
            return
            
        self.running = False
        
        if self.monitoring_task and not self.monitoring_task.done():
            self.monitoring_task.cancel()
            try:
                await asyncio.wait_for(self.monitoring_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
                
        # Завершаем все отслеживаемые активности
        self._finalize_all_activities()
        
        bot_logger.info("🔍 Автономный монитор активности остановлен")
        
    def _chunks(self, lst, size):
        """Разбивает список на чанки"""
        for i in range(0, len(lst), size):
            yield lst[i:i + size]
            
    async def _monitoring_loop(self):
        """Основной цикл мониторинга"""
        check_interval = 5  # Проверяем каждые 5 секунд
        
        while self.running:
            try:
                watchlist = watchlist_manager.get_all()
                if not watchlist:
                    await asyncio.sleep(check_interval)
                    continue
                    
                # Получаем данные монет батчами
                batch_size = config_manager.get('CHECK_BATCH_SIZE', 15)
                for batch in self._chunks(list(watchlist), batch_size):
                    if not self.running:
                        break
                        
                    try:
                        batch_data = await api_client.get_batch_coin_data(batch)
                        
                        for symbol, coin_data in batch_data.items():
                            if not self.running:
                                break
                                
                            if coin_data:
                                await self._process_coin_activity(symbol, coin_data)
                                
                    except Exception as e:
                        bot_logger.debug(f"Ошибка получения данных batch {batch}: {e}")
                        
                    await asyncio.sleep(0.2)  # Небольшая пауза между батчами
                    
                # Проверяем завершение активностей
                self._check_inactive_coins()
                
                await asyncio.sleep(check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                bot_logger.error(f"Ошибка в автономном мониторе: {e}")
                await asyncio.sleep(5)
                
    async def _process_coin_activity(self, symbol: str, coin_data: Dict):
        """Обрабатывает активность монеты"""
        current_time = time.time()
        is_active = coin_data.get('active', False)
        
        if is_active:
            # Монета активна
            if symbol not in self.tracked_coins:
                # Новая активность
                self.tracked_coins[symbol] = {
                    'start_time': current_time,
                    'last_active': current_time,
                    'data_points': [],
                    'max_volume': coin_data.get('volume', 0),
                    'total_trades': 0
                }
                bot_logger.debug(f"🔍 Автономный монитор: начата активность {symbol}")
                
            # Обновляем данные
            tracked = self.tracked_coins[symbol]
            tracked['last_active'] = current_time
            tracked['max_volume'] = max(tracked['max_volume'], coin_data.get('volume', 0))
            tracked['total_trades'] += coin_data.get('trades', 0)
            
            # Сохраняем данные для минутного интервала
            minute_key = int(current_time // 60) * 60
            tracked['data_points'].append({
                'timestamp': current_time,
                'minute': minute_key,
                'volume': coin_data.get('volume', 0),
                'trades': coin_data.get('trades', 0),
                'price': coin_data.get('price', 0),
                'change': coin_data.get('change', 0),
                'spread': coin_data.get('spread', 0),
                'natr': coin_data.get('natr', 0)
            })
            
            # Передаем в Session Recorder
            session_recorder.update_coin_activity(symbol, coin_data)
            
    def _check_inactive_coins(self):
        """Проверяет и завершает неактивные монеты"""
        current_time = time.time()
        inactive_threshold = 90  # 90 секунд без активности
        min_duration = 60  # Минимум 1 минута активности
        
        coins_to_finalize = []
        
        for symbol, tracked in list(self.tracked_coins.items()):
            time_since_active = current_time - tracked['last_active']
            total_duration = current_time - tracked['start_time']
            
            if time_since_active > inactive_threshold:
                if total_duration >= min_duration:
                    # Активность была достаточно долгой - сохраняем
                    coins_to_finalize.append(symbol)
                else:
                    # Слишком короткая активность - просто удаляем
                    del self.tracked_coins[symbol]
                    bot_logger.debug(f"🔍 Автономный монитор: удалена короткая активность {symbol} ({total_duration:.1f}s)")
                    
        # Финализируем длинные активности
        for symbol in coins_to_finalize:
            self._finalize_activity(symbol)
            
    def _finalize_activity(self, symbol: str):
        """Финализирует активность монеты"""
        if symbol not in self.tracked_coins:
            return
            
        tracked = self.tracked_coins[symbol]
        current_time = time.time()
        
        duration = current_time - tracked['start_time']
        data_points = len(tracked['data_points'])
        
        # Создаем сводку активности
        activity_summary = {
            'symbol': symbol,
            'start_time': tracked['start_time'],
            'end_time': current_time,
            'duration_seconds': duration,
            'duration_minutes': duration / 60,
            'data_points_count': data_points,
            'max_volume': tracked['max_volume'],
            'total_trades': tracked['total_trades'],
            'summary': f"Активность {symbol}: {duration/60:.1f} мин, макс. объем ${tracked['max_volume']:,.0f}, {data_points} точек данных"
        }
        
        # Логируем завершение
        bot_logger.info(
            f"🏁 Автономный монитор: завершена активность {symbol} - "
            f"{duration/60:.1f} мин, макс.объем ${tracked['max_volume']:,.0f}, "
            f"{data_points} обновлений"
        )
        
        # Удаляем из отслеживания
        del self.tracked_coins[symbol]
        
    def _finalize_all_activities(self):
        """Завершает все текущие активности"""
        for symbol in list(self.tracked_coins.keys()):
            tracked = self.tracked_coins[symbol]
            duration = time.time() - tracked['start_time']
            
            if duration >= 60:  # Только активности больше минуты
                self._finalize_activity(symbol)
            else:
                del self.tracked_coins[symbol]
                
    def get_stats(self) -> Dict:
        """Возвращает статистику автономного мониторинга"""
        current_time = time.time()
        
        active_count = len(self.tracked_coins)
        active_symbols = list(self.tracked_coins.keys())
        
        # Статистика по длительности
        durations = []
        for tracked in self.tracked_coins.values():
            duration = current_time - tracked['start_time']
            durations.append(duration)
            
        return {
            'running': self.running,
            'active_activities': active_count,
            'active_symbols': active_symbols,
            'avg_duration_minutes': (sum(durations) / len(durations) / 60) if durations else 0,
            'longest_activity_minutes': (max(durations) / 60) if durations else 0
        }


# Глобальный экземпляр
autonomous_monitor = AutonomousActivityMonitor()
