
import asyncio
import aiohttp
import time
from typing import Dict, List, Optional, Set
from concurrent.futures import ThreadPoolExecutor
from logger import bot_logger
from config import config_manager
from cache_manager import cache_manager

class OptimizedAPIClient:
    def __init__(self):
        self.base_url = "https://api.mexc.com/api/v3"
        self.session: Optional[aiohttp.ClientSession] = None
        self.rate_limiter = asyncio.Semaphore(30)  # Увеличиваем лимит
        self.executor = ThreadPoolExecutor(max_workers=8)
        self.last_request_time = 0
        self.failed_symbols = set()  # Кеш неудачных символов
        self.last_failed_cleanup = time.time()
        
    async def _get_session(self) -> aiohttp.ClientSession:
        """Получает единую HTTP сессию с правильным lifecycle"""
        if self.session is None or self.session.closed:
            # Оптимальная конфигурация для скорости
            timeout = aiohttp.ClientTimeout(
                total=8,  # Уменьшили общий timeout
                connect=3,
                sock_read=5
            )

            connector = aiohttp.TCPConnector(
                limit=50,  # Увеличили пул соединений
                limit_per_host=25,
                ttl_dns_cache=300,
                use_dns_cache=True,
                keepalive_timeout=30,
                enable_cleanup_closed=True,
                force_close=False
            )

            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={
                    'User-Agent': 'TradingBot/2.1-Ultra',
                    'Accept': 'application/json',
                    'Connection': 'keep-alive'
                }
            )
            bot_logger.debug("🔄 Оптимизированная HTTP сессия создана")

        return self.session

    async def _rate_limit(self):
        """Улучшенный rate limiting"""
        current_time = time.time()
        if current_time - self.last_request_time < 0.033:  # ~30 RPS
            await asyncio.sleep(0.033 - (current_time - self.last_request_time))
        self.last_request_time = time.time()

    async def get_batch_coin_data_ultra(self, symbols: List[str]) -> Dict[str, Optional[Dict]]:
        """Ультра-быстрое получение данных монет с кешированием неудачных попыток"""
        # Очищаем кеш неудачных символов каждые 5 минут
        if time.time() - self.last_failed_cleanup > 300:
            self.failed_symbols.clear()
            self.last_failed_cleanup = time.time()
            
        # Фильтруем заведомо неудачные символы
        valid_symbols = [s for s in symbols if s not in self.failed_symbols]
        
        if not valid_symbols:
            return {}

        async with self.rate_limiter:
            # Получаем все тикеры одним запросом
            all_tickers = await self._get_all_tickers_batch()
            if not all_tickers:
                return {}
                
            # Создаем индекс доступных символов
            available_symbols = {
                ticker['symbol'].replace('USDT', ''): ticker 
                for ticker in all_tickers 
                if ticker['symbol'].endswith('USDT')
            }
            
            # Обновляем кеш неудачных символов
            for symbol in valid_symbols:
                if symbol not in available_symbols:
                    self.failed_symbols.add(symbol)
            
            # Обрабатываем только доступные символы
            tasks = []
            for symbol in valid_symbols:
                if symbol in available_symbols:
                    ticker_data = available_symbols[symbol]
                    cache_manager.set_ticker_cache(symbol, ticker_data)
                    task = self._process_single_coin_ultra(symbol, ticker_data)
                    tasks.append((symbol, task))

            # Выполняем параллельно с контролем нагрузки
            semaphore = asyncio.Semaphore(20)
            
            async def limited_task(symbol, task):
                async with semaphore:
                    return symbol, await task

            results = await asyncio.gather(
                *[limited_task(symbol, task) for symbol, task in tasks],
                return_exceptions=True
            )
            
            # Собираем результаты
            coin_data = {}
            for result in results:
                if isinstance(result, Exception):
                    continue
                symbol, data = result
                if data:
                    coin_data[symbol] = data
                    
            return coin_data

    async def _get_all_tickers_batch(self) -> Optional[List]:
        """Получает все тикеры одним запросом"""
        try:
            await self._rate_limit()
            session = await self._get_session()
            
            async with session.get(f"{self.base_url}/ticker/24hr") as response:
                if response.status == 200:
                    return await response.json()
                else:
                    bot_logger.debug(f"Ошибка получения тикеров: {response.status}")
                    return None
                    
        except Exception as e:
            bot_logger.debug(f"Исключение при получении тикеров: {type(e).__name__}")
            return None

    async def _process_single_coin_ultra(self, symbol: str, ticker_data: Dict) -> Optional[Dict]:
        """Ультра-быстрая обработка одной монеты"""
        try:
            # Получаем дополнительные данные параллельно
            tasks = [
                self._get_book_ticker_fast(symbol),
                self._get_klines_fast(symbol)
            ]
            
            book_data, klines_data = await asyncio.gather(*tasks, return_exceptions=True)
            
            if isinstance(book_data, Exception) or not book_data:
                return None
                
            # Базовые данные из тикера
            price = float(ticker_data['lastPrice'])
            volume_24h = float(ticker_data['quoteVolume'])
            change_24h = float(ticker_data['priceChangePercent'])
            
            # Расчет спреда
            bid_price = float(book_data['bidPrice'])
            ask_price = float(book_data['askPrice'])
            spread = ((ask_price - bid_price) / bid_price) * 100 if bid_price > 0 else 0
            
            # Данные из klines или fallback
            if isinstance(klines_data, Exception) or not klines_data:
                volume_1m = volume_24h / 1440  # Примерная оценка
                change_1m = change_24h / 24  # Примерная оценка
                natr = 0.5  # Минимальное значение
            else:
                last_candle = klines_data[-1]
                volume_1m = float(last_candle[7])  # Реальный 1м объем
                
                # Реальный 1м change и NATR
                open_price = float(last_candle[1])
                close_price = float(last_candle[4])
                high_price = float(last_candle[2])
                low_price = float(last_candle[3])
                
                change_1m = ((close_price - open_price) / open_price) * 100 if open_price > 0 else 0
                
                true_range = max(
                    high_price - low_price,
                    abs(high_price - open_price),
                    abs(low_price - open_price)
                )
                natr = (true_range / open_price) * 100 if open_price > 0 else 0
            
            # Быстрая проверка активности
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
                'price': price,
                'volume': volume_1m,
                'change': change_1m,
                'spread': spread,
                'natr': natr,
                'trades': 0,  # Убираем медленный подсчет сделок
                'active': is_active,
                'has_recent_trades': volume_1m > 0,
                'timestamp': time.time()
            }
            
        except Exception as e:
            bot_logger.debug(f"Ошибка обработки {symbol}: {type(e).__name__}")
            return None

    async def _get_book_ticker_fast(self, symbol: str) -> Optional[Dict]:
        """Быстрое получение book ticker"""
        try:
            await self._rate_limit()
            session = await self._get_session()
            
            params = {'symbol': f"{symbol}USDT"}
            async with session.get(f"{self.base_url}/ticker/bookTicker", params=params) as response:
                if response.status == 200:
                    return await response.json()
                return None
                
        except Exception:
            return None

    async def _get_klines_fast(self, symbol: str) -> Optional[List]:
        """Быстрое получение klines"""
        try:
            await self._rate_limit()
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
                
        except Exception:
            return None

    async def get_optimized_coin_data(self, symbol: str) -> Optional[Dict]:
        """Получает данные одной монеты (совместимость)"""
        if symbol in self.failed_symbols:
            return None
            
        batch_result = await self.get_batch_coin_data_ultra([symbol])
        return batch_result.get(symbol)

    async def close(self):
        """Правильное закрытие с предотвращением утечек"""
        if self.session and not self.session.closed:
            try:
                await self.session.close()
                # Ждем завершения всех соединений
                await asyncio.sleep(0.25)
                bot_logger.debug("Оптимизированный API клиент закрыт")
            except Exception as e:
                bot_logger.debug(f"Ошибка закрытия клиента: {type(e).__name__}")
            finally:
                self.session = None
                
        self.executor.shutdown(wait=False)

# Глобальный экземпляр
optimized_api_client = OptimizedAPIClient()
