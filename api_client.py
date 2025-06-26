import asyncio
import aiohttp
import time
from typing import Dict, List, Optional
from logger import bot_logger
from config import config_manager
from cache_manager import cache_manager
from metrics_manager import metrics_manager
from circuit_breaker import api_circuit_breakers
from data_validator import data_validator

class APIClient:
    def __init__(self):
        self.base_url = "https://api.mexc.com/api/v3"
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_request_time = 0
        self.request_count = 0
        self.start_time = time.time()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Получает или создает HTTP сессию с правильной конфигурацией"""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(
                total=8,  # Уменьшили timeout
                connect=3,
                sock_read=5
            )

            connector = aiohttp.TCPConnector(
                limit=50,  # Увеличили лимит
                limit_per_host=25,
                ttl_dns_cache=300,
                use_dns_cache=True,
                keepalive_timeout=30,
                enable_cleanup_closed=True,
                force_close=False  # Важно для предотвращения утечек
            )

            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={
                    'User-Agent': 'TradingBot/2.1',
                    'Accept': 'application/json',
                    'Connection': 'keep-alive'
                }
            )
            bot_logger.debug("🔄 HTTP сессия создана")

        return self.session

    async def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Выполняет HTTP запрос с исправленной обработкой ошибок"""
        url = f"{self.base_url}{endpoint}"
        await self._rate_limit()

        # Определяем Circuit Breaker
        circuit_breaker = None
        for cb_name, cb in api_circuit_breakers.items():
            if cb_name in endpoint:
                circuit_breaker = cb
                break

        max_retries = 2  # Уменьшили количество retry

        async def _execute_request():
            session = await self._get_session()
            start_time = time.time()

            async with session.get(url, params=params) as response:
                request_time = time.time() - start_time

                bot_logger.api_request("GET", url, response.status, request_time)
                metrics_manager.record_api_request(endpoint, request_time, response.status)

                if response.status == 200:
                    return await response.json()
                elif response.status == 429:
                    raise Exception(f"Rate limit hit for {endpoint}")
                else:
                    raise Exception(f"API error {response.status} for {endpoint}")

        for attempt in range(max_retries + 1):
            try:
                if circuit_breaker:
                    return await circuit_breaker.call(_execute_request)
                else:
                    return await _execute_request()

            except Exception as e:
                error_msg = str(e).lower()

                if "rate limit" in error_msg and attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)  # Экспоненциальная задержка
                    continue
                elif attempt < max_retries:
                    await asyncio.sleep(0.5)
                    continue
                else:
                    bot_logger.debug(f"Request failed after {max_retries + 1} attempts: {endpoint}")
                    return None

        return None

    async def get_ticker_data(self, symbol: str) -> Optional[Dict]:
        """Получает данные тикера с кешированием"""
        cached_data = cache_manager.get_ticker_cache(symbol)
        if cached_data:
            return cached_data

        params = {'symbol': f"{symbol}USDT"}
        data = await self._make_request("/ticker/24hr", params)

        if data:
            cache_manager.set_ticker_cache(symbol, data, 30)  # Кеш на 30 сек

        return data

    async def get_book_ticker(self, symbol: str) -> Optional[Dict]:
        """Получает данные книги ордеров"""
        params = {'symbol': f"{symbol}USDT"}
        return await self._make_request("/ticker/bookTicker", params)

    async def get_klines(self, symbol: str, interval: str = "1m", limit: int = 2) -> Optional[List]:
        """Получает данные свечей"""
        params = {
            'symbol': f"{symbol}USDT",
            'interval': interval,
            'limit': limit
        }
        return await self._make_request("/klines", params)

    async def get_multiple_tickers_batch(self, symbols: List[str]) -> Dict[str, Optional[Dict]]:
        """Получает данные тикеров для списка символов"""
        results = {}
        uncached_symbols = []

        # Проверяем кеш
        for symbol in symbols:
            cached_data = cache_manager.get_ticker_cache(symbol)
            if cached_data:
                results[symbol] = cached_data
            else:
                uncached_symbols.append(symbol)

        if not uncached_symbols:
            return results

        try:
            # Получаем все тикеры одним запросом
            all_tickers = await self._make_request("/ticker/24hr")
            if all_tickers:
                ticker_dict = {
                    ticker['symbol'].replace('USDT', ''): ticker 
                    for ticker in all_tickers 
                    if ticker['symbol'].endswith('USDT')
                }

                for symbol in uncached_symbols:
                    ticker_data = ticker_dict.get(symbol)
                    results[symbol] = ticker_data
                    if ticker_data:
                        cache_manager.set_ticker_cache(symbol, ticker_data, 30)
            else:
                # Fallback
                for symbol in uncached_symbols:
                    ticker_data = await self.get_ticker_data(symbol)
                    results[symbol] = ticker_data

        except Exception as e:
            bot_logger.error(f"Ошибка batch запроса: {e}")
            for symbol in uncached_symbols:
                try:
                    ticker_data = await self.get_ticker_data(symbol)
                    results[symbol] = ticker_data
                except Exception:
                    results[symbol] = None

        return results

    def _calculate_natr(self, klines: List) -> float:
        """Вычисляет NATR"""
        if not klines or len(klines) < 2:
            return 0.0

        try:
            current = klines[-1]
            previous = klines[-2]

            high = float(current[2])
            low = float(current[3])
            prev_close = float(previous[4])
            close = float(current[4])

            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            true_range = max(tr1, tr2, tr3)

            if close > 0:
                natr = (true_range / close) * 100
                return round(natr, 2)
            return 0.0
        except (IndexError, ValueError, TypeError):
            return 0.0

    async def get_recent_trades(self, symbol: str, limit: int = 500) -> Optional[List]:
        """Получает последние сделки"""
        params = {
            'symbol': f"{symbol}USDT",
            'limit': limit
        }
        return await self._make_request("/trades", params)

    async def get_trades_last_minute(self, symbol: str) -> int:
        """Получает количество сделок за последнюю минуту"""
        try:
            trades = await self.get_recent_trades(symbol, 1000)
            if not trades:
                return 0

            current_time = time.time() * 1000
            minute_ago = current_time - 60000

            trades_count = 0
            for trade in trades:
                if isinstance(trade, dict) and 'time' in trade:
                    trade_time = int(trade['time'])
                    if trade_time >= minute_ago:
                        trades_count += 1
                    else:
                        break

            return trades_count

        except Exception as e:
            bot_logger.debug(f"Ошибка получения сделок для {symbol}: {e}")
            return 0

    async def get_coin_data(self, symbol: str) -> Optional[Dict]:
        """Получает полные данные по монете с исправленной обработкой ошибок"""
        try:
            # Получаем данные параллельно
            tasks = [
                self.get_ticker_data(symbol),
                self.get_book_ticker(symbol),
                self.get_klines(symbol, "1m", 2),
                self.get_trades_last_minute(symbol)
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)
            ticker_data, book_data, klines_data, trades_1m = results

            # Проверяем результаты и обрабатываем исключения
            if isinstance(ticker_data, Exception) or not ticker_data:
                bot_logger.debug(f"Нет данных тикера для {symbol}")
                return None

            if isinstance(book_data, Exception) or not book_data:
                bot_logger.debug(f"Нет book ticker для {symbol}")
                return None

            # Обрабатываем klines
            if isinstance(klines_data, Exception) or not klines_data:
                volume_1m_usdt = 0
                change_1m = 0
                natr = 0
            else:
                last_candle = klines_data[-1] if klines_data else None
                if last_candle:
                    volume_1m_usdt = float(last_candle[7])
                    open_price = float(last_candle[1])
                    close_price = float(last_candle[4])
                    high_price = float(last_candle[2])
                    low_price = float(last_candle[3])

                    change_1m = ((close_price - open_price) / open_price) * 100 if open_price > 0 else 0

                    if open_price > 0:
                        true_range = max(
                            high_price - low_price,
                            abs(high_price - open_price),
                            abs(low_price - open_price)
                        )
                        natr = (true_range / open_price) * 100
                    else:
                        natr = 0
                else:
                    volume_1m_usdt = 0
                    change_1m = 0
                    natr = 0

            # Обрабатываем сделки
            trades_count = 0
            if not isinstance(trades_1m, Exception) and isinstance(trades_1m, int):
                trades_count = trades_1m

            # Рассчитываем спред
            bid_price = float(book_data['bidPrice'])
            ask_price = float(book_data['askPrice'])
            spread = ((ask_price - bid_price) / bid_price) * 100 if bid_price > 0 else 0

            # Проверяем активность
            vol_thresh = config_manager.get('VOLUME_THRESHOLD')
            spread_thresh = config_manager.get('SPREAD_THRESHOLD')
            natr_thresh = config_manager.get('NATR_THRESHOLD')

            is_active = (
                volume_1m_usdt >= vol_thresh and
                spread >= spread_thresh and
                natr >= natr_thresh
            )

            has_recent_trades = trades_count > 0
            price = float(ticker_data['lastPrice']) if ticker_data else 0

            coin_data = {
                'symbol': symbol,
                'price': price,
                'volume': volume_1m_usdt,
                'change': change_1m,
                'spread': spread,
                'natr': natr,
                'trades': trades_count,
                'active': is_active,
                'has_recent_trades': has_recent_trades,
                'timestamp': time.time()
            }

            # Валидируем данные
            if not data_validator.validate_coin_data(coin_data):
                bot_logger.debug(f"Данные для {symbol} не прошли валидацию")
                return None

            return coin_data

        except asyncio.CancelledError:
            bot_logger.debug(f"Запрос для {symbol} был отменен")
            return None
        except Exception as e:
            bot_logger.debug(f"Ошибка получения данных для {symbol}: {type(e).__name__}")
            return None

    async def _rate_limit(self):
        """Улучшенный rate limiting"""
        interval = time.time() - self.last_request_time
        min_interval = 0.033  # ~30 RPS
        if interval < min_interval:
            await asyncio.sleep(min_interval - interval)
        self.last_request_time = time.time()

    async def get_current_price_fast(self, symbol: str) -> Optional[float]:
        """Быстрое получение цены с кешированием"""
        try:
            cached_price = cache_manager.get_price_cache(symbol)
            if cached_price:
                return cached_price

            ticker_data = await self.get_ticker_data(symbol)
            if ticker_data and 'lastPrice' in ticker_data:
                price = float(ticker_data['lastPrice'])
                cache_manager.set_price_cache(symbol, price, 30)
                return price
            return None
        except Exception as e:
            bot_logger.debug(f"Ошибка получения цены {symbol}: {e}")
            return None

    async def close(self):
        """Правильное закрытие с предотвращением утечек памяти"""
        if self.session and not self.session.closed:
            try:
                # Закрываем все соединения
                await self.session.close()

                # Ждем завершения всех соединений
                await asyncio.sleep(0.1)

                bot_logger.debug("HTTP сессия корректно закрыта")
            except Exception as e:
                bot_logger.debug(f"Ошибка при закрытии сессии: {type(e).__name__}")
            finally:
                self.session = None

# Глобальный экземпляр
api_client = APIClient()