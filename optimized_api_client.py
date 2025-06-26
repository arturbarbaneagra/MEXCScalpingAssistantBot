
import asyncio
import aiohttp
import time
from typing import Dict, List, Optional, Set
from concurrent.futures import ThreadPoolExecutor
from logger import bot_logger
from config import config_manager
from cache_manager import cache_manager
from websocket_client import ws_client

class OptimizedAPIClient:
    def __init__(self):
        self.base_url = "https://api.mexc.com/api/v3"
        self.session_pool: List[aiohttp.ClientSession] = []
        self.pool_size = 10  # Увеличиваем пул
        self.current_session = 0
        self.rate_limiter = asyncio.Semaphore(25)  # Максимальный лимит
        self.executor = ThreadPoolExecutor(max_workers=10)
        
    async def _get_session(self) -> aiohttp.ClientSession:
        """Получает сессию из пула"""
        if not self.session_pool:
            await self._init_session_pool()
            
        # Циклически используем сессии из пула
        session = self.session_pool[self.current_session]
        self.current_session = (self.current_session + 1) % self.pool_size
        
        if session.closed:
            # Пересоздаем закрытую сессию
            session = await self._create_session()
            self.session_pool[self.current_session - 1] = session
            
        return session

    async def _init_session_pool(self):
        """Инициализирует пул сессий"""
        self.session_pool = []
        for _ in range(self.pool_size):
            session = await self._create_session()
            self.session_pool.append(session)
        bot_logger.info(f"🔄 Создан пул из {self.pool_size} HTTP сессий")

    async def _create_session(self) -> aiohttp.ClientSession:
        """Создает оптимизированную HTTP сессию"""
        timeout = aiohttp.ClientTimeout(
            total=5,  # Минимальный timeout для скорости
            connect=2,
            sock_read=3
        )

        connector = aiohttp.TCPConnector(
            limit=100,  # Увеличиваем лимит соединений
            limit_per_host=50,
            ttl_dns_cache=600,
            use_dns_cache=True,
            keepalive_timeout=60,
            enable_cleanup_closed=True,
            force_close=False
        )

        return aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={
                'User-Agent': 'TradingBot/2.1-Optimized',
                'Accept': 'application/json',
                'Connection': 'keep-alive'
            }
        )

    async def get_batch_klines(self, symbols: List[str]) -> Dict[str, Optional[List]]:
        """Получает klines для множества символов параллельно"""
        async with self.rate_limiter:
            tasks = []
            
            # Создаем задачи для параллельного выполнения
            for symbol in symbols:
                task = self._get_single_kline(symbol)
                tasks.append(task)
            
            # Выполняем все запросы параллельно с ограничением
            semaphore = asyncio.Semaphore(10)  # Максимум 10 одновременных запросов
            
            async def limited_task(task, sym):
                async with semaphore:
                    return sym, await task
            
            results = await asyncio.gather(
                *[limited_task(task, sym) for task, sym in zip(tasks, symbols)],
                return_exceptions=True
            )
            
            # Обрабатываем результаты
            klines_data = {}
            for result in results:
                if isinstance(result, Exception):
                    continue
                symbol, kline_data = result
                klines_data[symbol] = kline_data
                
            return klines_data

    async def _get_single_kline(self, symbol: str) -> Optional[List]:
        """Получает kline для одного символа"""
        try:
            session = await self._get_session()
            params = {
                'symbol': f"{symbol}USDT",
                'interval': "1m",
                'limit': 2
            }
            
            async with session.get(f"{self.base_url}/klines", params=params) as response:
                if response.status == 200:
                    return await response.json()
                return None
                
        except Exception as e:
            bot_logger.debug(f"Ошибка получения kline для {symbol}: {e}")
            return None

    async def get_batch_trades(self, symbols: List[str]) -> Dict[str, int]:
        """Получает количество сделок для множества символов"""
        async with self.rate_limiter:
            # Используем ThreadPoolExecutor для CPU-интенсивных операций
            loop = asyncio.get_event_loop()
            
            tasks = []
            for symbol in symbols:
                task = loop.run_in_executor(
                    self.executor,
                    self._process_trades_sync,
                    symbol
                )
                tasks.append(task)
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            trades_data = {}
            for i, result in enumerate(results):
                if not isinstance(result, Exception):
                    trades_data[symbols[i]] = result
                else:
                    trades_data[symbols[i]] = 0
                    
            return trades_data

    def _process_trades_sync(self, symbol: str) -> int:
        """Синхронная обработка сделок (для ThreadPoolExecutor)"""
        try:
            # Здесь можно использовать кеш или WebSocket данные
            # для подсчета сделок за последнюю минуту
            return 0  # Заглушка
        except:
            return 0

    async def get_optimized_coin_data(self, symbol: str) -> Optional[Dict]:
        """Получает оптимизированные данные монеты из кеша и WebSocket"""
        try:
            # Сначала пытаемся получить из кеша (WebSocket данные)
            ticker_data = cache_manager.get_ticker_cache(symbol)
            book_data = cache_manager.get_book_ticker_cache(symbol)
            
            if not ticker_data or not book_data:
                # Fallback на HTTP API только при необходимости
                return await self._fallback_coin_data(symbol)
            
            # Получаем klines только если нужно (для NATR и точного объема)
            kline_data = await self._get_single_kline(symbol)
            if not kline_data:
                return None
                
            # Быстрая обработка данных
            volume_1m = float(kline_data[-1][7]) if kline_data else 0
            
            # Рассчитываем изменение из WebSocket данных
            change_1m = float(ticker_data.get('priceChangePercent', 0))
            
            # Быстрый расчет NATR
            natr = self._fast_natr_calculation(kline_data) if kline_data else 0
            
            # Расчет спреда
            bid_price = float(book_data['bidPrice'])
            ask_price = float(book_data['askPrice'])
            spread = ((ask_price - bid_price) / bid_price) * 100 if bid_price > 0 else 0
            
            # Получаем количество сделок (можно оптимизировать через WebSocket)
            trades_count = cache_manager.get_trades_cache(symbol) or 0
            
            # Проверяем активность
            vol_thresh = config_manager.get('VOLUME_THRESHOLD')
            spread_thresh = config_manager.get('SPREAD_THRESHOLD') 
            natr_thresh = config_manager.get('NATR_THRESHOLD')
            
            is_active = (
                volume_1m >= vol_thresh and
                spread >= spread_thresh and
                natr >= natr_thresh
            )
            
            return {
                'symbol': symbol,
                'price': float(ticker_data['lastPrice']),
                'volume': volume_1m,
                'change': change_1m,
                'spread': spread,
                'natr': natr,
                'trades': trades_count,
                'active': is_active,
                'has_recent_trades': trades_count > 0,
                'timestamp': time.time()
            }
            
        except Exception as e:
            bot_logger.error(f"Ошибка получения оптимизированных данных для {symbol}: {e}")
            return None

    def _fast_natr_calculation(self, klines: List) -> float:
        """Быстрый расчет NATR"""
        if not klines or len(klines) < 2:
            return 0.0
            
        try:
            current = klines[-1]
            previous = klines[-2]
            
            high = float(current[2])
            low = float(current[3])
            prev_close = float(previous[4])
            close = float(current[4])
            
            true_range = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            
            return (true_range / close) * 100 if close > 0 else 0.0
        except:
            return 0.0

    async def _fallback_coin_data(self, symbol: str) -> Optional[Dict]:
        """Fallback на обычный HTTP API"""
        # Используем старый метод как запасной вариант
        from api_client import api_client
        return await api_client.get_coin_data(symbol)

    async def close(self):
        """Закрывает все сессии"""
        for session in self.session_pool:
            if not session.closed:
                await session.close()
        self.executor.shutdown(wait=True)
        bot_logger.info("Оптимизированный API клиент закрыт")

# Глобальный экземпляр
optimized_api_client = OptimizedAPIClient()
