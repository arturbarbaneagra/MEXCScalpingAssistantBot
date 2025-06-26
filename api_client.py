import asyncio
import time
import aiohttp
from typing import Optional, Dict, List, Any
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
            # Правильная конфигурация таймаутов
            timeout = aiohttp.ClientTimeout(
                total=config_manager.get('API_TIMEOUT', 12),
                connect=5,
                sock_read=10
            )

            # Настройки коннектора для оптимизации
            connector = aiohttp.TCPConnector(
                limit=20,
                limit_per_host=10,
                ttl_dns_cache=300,
                use_dns_cache=True,
                keepalive_timeout=30,
                enable_cleanup_closed=True
            )

            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={
                    'User-Agent': 'TradingBot/2.0',
                    'Accept': 'application/json'
                }
            )
            bot_logger.debug("🔄 HTTP сессия создана")

        return self.session

    async def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Выполняет HTTP запрос с обработкой ошибок, retry логикой и Circuit Breaker"""
        url = f"{self.base_url}{endpoint}"

        # Rate limiting
        await self._rate_limit()

        # Определяем Circuit Breaker по endpoint
        circuit_breaker = None
        for cb_name, cb in api_circuit_breakers.items():
            if cb_name in endpoint:
                circuit_breaker = cb
                break
        
        max_retries = config_manager.get('MAX_RETRIES', 2)

        async def _execute_request():
            """Внутренняя функция для выполнения запроса"""
            session = await self._get_session()
            async with session.get(url, params=params) as response:
                request_time = time.time() - start_time
                
                # Логируем запрос и записываем метрики
                bot_logger.api_request("GET", url, response.status, request_time)
                metrics_manager.record_api_request(endpoint, request_time, response.status)

                if response.status == 200:
                    data = await response.json()
                    return data
                elif response.status == 429:  # Rate limit
                    raise Exception(f"Rate limit hit for {endpoint}")
                else:
                    raise Exception(f"API error {response.status} for {endpoint}")

        for attempt in range(max_retries + 1):
            start_time = time.time()

            try:
                # Используем Circuit Breaker если доступен
                if circuit_breaker:
                    return await circuit_breaker.call(_execute_request)
                else:
                    return await _execute_request()

            except Exception as e:
                error_msg = str(e).lower()
                
                if "rate limit" in error_msg and attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                elif attempt < max_retries:
                    await asyncio.sleep(1)
                    # Пересоздаем сессию при ошибке
                    await self.close()
                    continue
                else:
                    return None

            except asyncio.TimeoutError:
                bot_logger.debug(f"Timeout on attempt {attempt + 1} for {endpoint}")
                if attempt < max_retries:
                    await asyncio.sleep(1)
                    continue
                return None
            except aiohttp.ClientError as e:
                bot_logger.debug(f"Client error on attempt {attempt + 1}: {type(e).__name__}")
                if attempt < max_retries:
                    await asyncio.sleep(1)
                    # Пересоздаем сессию при ошибке
                    await self.close()
                    continue
                return None
            except Exception as e:
                error_msg = str(e)
                # Скрываем частые ошибки timeout context manager
                if "timeout context manager" in error_msg.lower():
                    bot_logger.debug(f"Timeout context error on attempt {attempt + 1}")
                else:
                    bot_logger.debug(f"Request exception on attempt {attempt + 1}: {type(e).__name__}")

                if attempt < max_retries:
                    await asyncio.sleep(1)
                    # Пересоздаем сессию при ошибке
                    await self.close()
                    continue
                return None

        return None

    async def get_ticker_data(self, symbol: str) -> Optional[Dict]:
        """Получает данные тикера для символа с кешированием"""
        # Проверяем кеш
        cached_data = cache_manager.get_ticker_cache(symbol)
        if cached_data:
            return cached_data
            
        # Запрашиваем данные
        params = {'symbol': f"{symbol}USDT"}
        data = await self._make_request("/ticker/24hr", params)
        
        # Сохраняем в кеш
        if data:
            cache_manager.set_ticker_cache(symbol, data)
            
        return data

    async def get_book_ticker(self, symbol: str) -> Optional[Dict]:
        """Получает данные книги ордеров (bid/ask)"""
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
        """Получает данные тикеров для списка символов (ультра оптимизированная версия)"""
        results = {}
        
        try:
            # Сразу получаем ВСЕ тикеры одним запросом (самый быстрый способ)
            all_tickers = await self._make_request("/ticker/24hr")
            if all_tickers:
                # Создаем индекс по символам
                ticker_dict = {ticker['symbol'].replace('USDT', ''): ticker 
                             for ticker in all_tickers 
                             if ticker['symbol'].endswith('USDT')}

                # Заполняем результаты для запрошенных символов
                for symbol in symbols:
                    ticker_data = ticker_dict.get(symbol)
                    results[symbol] = ticker_data
                    # Кешируем все полученные данные
                    if ticker_data:
                        cache_manager.set_ticker_cache(symbol, ticker_data)
            else:
                # Fallback - используем кеш и индивидуальные запросы
                for symbol in symbols:
                    cached_data = cache_manager.get_ticker_cache(symbol)
                    if cached_data:
                        results[symbol] = cached_data
                    else:
                        ticker_data = await self.get_ticker_data(symbol)
                        results[symbol] = ticker_data

        except Exception as e:
            bot_logger.error(f"Ошибка batch запроса всех тикеров: {e}")
            # Fallback - индивидуальные запросы с кешем
            for symbol in symbols:
                try:
                    cached_data = cache_manager.get_ticker_cache(symbol)
                    if cached_data:
                        results[symbol] = cached_data
                    else:
                        ticker_data = await self.get_ticker_data(symbol)
                        results[symbol] = ticker_data
                except Exception as sym_e:
                    bot_logger.error(f"Ошибка получения тикера {symbol}: {sym_e}")
                    results[symbol] = None

        return results

    async def get_batch_coin_data(self, symbols: List[str]) -> Dict[str, Optional[Dict]]:
        """Получает данные для группы монет с максимальной оптимизацией"""
        results = {}
        
        try:
            # 1. Получаем все book tickers одним запросом
            book_tickers_task = self._make_request("/ticker/bookTicker")
            
            # 2. Создаем задачи для klines и trades параллельно
            klines_tasks = {}
            trades_tasks = {}
            
            for symbol in symbols:
                klines_tasks[symbol] = self.get_klines(symbol, "1m", 2)
                trades_tasks[symbol] = self.get_trades_last_minute(symbol)
            
            # 3. Выполняем все запросы параллельно
            book_tickers_data = await book_tickers_task
            klines_results = await asyncio.gather(*klines_tasks.values(), return_exceptions=True)
            trades_results = await asyncio.gather(*trades_tasks.values(), return_exceptions=True)
            
            # 4. Создаем индекс book tickers
            book_ticker_dict = {}
            if book_tickers_data:
                for book_ticker in book_tickers_data:
                    if book_ticker['symbol'].endswith('USDT'):
                        symbol = book_ticker['symbol'].replace('USDT', '')
                        book_ticker_dict[symbol] = book_ticker
            
            # 5. Собираем результаты
            klines_dict = dict(zip(symbols, klines_results))
            trades_dict = dict(zip(symbols, trades_results))
            
            for symbol in symbols:
                try:
                    # Получаем данные для символа
                    book_data = book_ticker_dict.get(symbol)
                    klines_data = klines_dict.get(symbol)
                    trades_1m = trades_dict.get(symbol)
                    
                    if not book_data or isinstance(klines_data, Exception) or not klines_data:
                        results[symbol] = None
                        continue
                    
                    # Обрабатываем данные
                    last_candle = klines_data[-1]
                    price = float(last_candle[4])  # close price
                    volume_1m_usdt = float(last_candle[7])  # quote volume
                    
                    open_price = float(last_candle[1])
                    close_price = float(last_candle[4])
                    high_price = float(last_candle[2])
                    low_price = float(last_candle[3])
                    
                    # Изменение за 1 минуту
                    change_1m = ((close_price - open_price) / open_price) * 100 if open_price > 0 else 0
                    
                    # NATR
                    if open_price > 0:
                        true_range = max(
                            high_price - low_price,
                            abs(high_price - open_price),
                            abs(low_price - open_price)
                        )
                        natr = (true_range / open_price) * 100
                    else:
                        natr = 0
                    
                    # Спред
                    bid_price = float(book_data['bidPrice'])
                    ask_price = float(book_data['askPrice'])
                    spread = ((ask_price - bid_price) / bid_price) * 100 if bid_price > 0 else 0
                    
                    # Количество сделок
                    trades_count = trades_1m if isinstance(trades_1m, int) else 0
                    
                    # Проверяем активность
                    vol_thresh = config_manager.get('VOLUME_THRESHOLD')
                    spread_thresh = config_manager.get('SPREAD_THRESHOLD')
                    natr_thresh = config_manager.get('NATR_THRESHOLD')
                    
                    is_active = (
                        volume_1m_usdt >= vol_thresh and
                        spread >= spread_thresh and
                        natr >= natr_thresh
                    )
                    
                    coin_data = {
                        'symbol': symbol,
                        'price': price,
                        'volume': volume_1m_usdt,
                        'change': change_1m,
                        'spread': spread,
                        'natr': natr,
                        'trades': trades_count,
                        'active': is_active,
                        'has_recent_trades': trades_count > 0,
                        'timestamp': time.time()
                    }
                    
                    # Валидируем данные
                    if data_validator.validate_coin_data(coin_data):
                        results[symbol] = coin_data
                    else:
                        results[symbol] = None
                        
                except Exception as e:
                    bot_logger.error(f"Ошибка обработки данных для {symbol}: {e}")
                    results[symbol] = None
            
        except Exception as e:
            bot_logger.error(f"Ошибка batch получения данных: {e}")
            # Fallback - используем старый метод
            for symbol in symbols:
                results[symbol] = await self.get_coin_data(symbol)
        
        return results

    def _calculate_natr(self, klines: List) -> float:
        """Вычисляет NATR (Normalized Average True Range)"""
        if not klines or len(klines) < 2:
            return 0.0

        try:
            # klines формат: [timestamp, open, high, low, close, volume, ...]
            current = klines[-1]  # Последняя свеча
            previous = klines[-2]  # Предыдущая свеча

            high = float(current[2])
            low = float(current[3])
            prev_close = float(previous[4])
            close = float(current[4])

            # True Range
            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            true_range = max(tr1, tr2, tr3)

            # Normalized по цене закрытия
            if close > 0:
                natr = (true_range / close) * 100
                return round(natr, 2)
            return 0.0
        except (IndexError, ValueError, TypeError):
            return 0.0

    async def get_recent_trades(self, symbol: str, limit: int = 500) -> Optional[List]:
        """Получает последние сделки для символа"""
        params = {
            'symbol': f"{symbol}USDT",
            'limit': limit
        }
        return await self._make_request("/trades", params)

    async def get_trades_last_minute(self, symbol: str) -> int:
        """Получает количество сделок за последнюю минуту с кешированием"""
        try:
            # Проверяем кеш сделок
            cached_trades = cache_manager.get_trades_cache(symbol)
            if cached_trades is not None:
                return cached_trades

            # Получаем последние сделки
            trades = await self.get_recent_trades(symbol, 500)  # Уменьшили лимит
            if not trades:
                cache_manager.set_trades_cache(symbol, 0)
                return 0

            current_time = time.time() * 1000  # в миллисекундах
            minute_ago = current_time - 60000  # 60 секунд назад

            # Считаем сделки за последнюю минуту
            trades_count = 0
            for trade in trades:
                if isinstance(trade, dict) and 'time' in trade:
                    trade_time = int(trade['time'])
                    if trade_time >= minute_ago:
                        trades_count += 1
                    else:
                        # Сделки идут по убыванию времени, можно прерывать
                        break

            # Кешируем результат
            cache_manager.set_trades_cache(symbol, trades_count)
            bot_logger.debug(f"{symbol}: Сделок за последнюю минуту: {trades_count}")
            return trades_count

        except Exception as e:
            bot_logger.error(f"Ошибка получения сделок для {symbol}: {e}")
            cache_manager.set_trades_cache(symbol, 0)
            return 0

    async def get_coin_data(self, symbol: str) -> Optional[Dict]:
        """Получает полные данные по монете для анализа (только 1-минутные данные)"""
        try:
            # Получаем данные параллельно (3 запроса вместо 4)
            tasks = [
                self.get_book_ticker(symbol),          # 1. Спред (bid/ask)
                self.get_klines(symbol, "1m", 2),      # 2. 1-минутные данные
                self.get_trades_last_minute(symbol)    # 3. Сделки за минуту
            ]

            book_data, klines_data, trades_1m = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Цену берем из klines (более эффективно)
            ticker_data = None
            if not isinstance(klines_data, Exception) and klines_data:
                # Создаем ticker_data из последней свечи
                last_candle = klines_data[-1]
                ticker_data = {'lastPrice': last_candle[4]}  # close price

            # Проверяем результаты
            if isinstance(ticker_data, Exception) or not ticker_data:
                bot_logger.warning(f"Нет данных цены для {symbol}")
                return None

            if isinstance(book_data, Exception) or not book_data:
                bot_logger.warning(f"Нет book ticker для {symbol}")
                return None

            if isinstance(klines_data, Exception) or not klines_data:
                bot_logger.warning(f"Нет 1м klines для {symbol}")
                volume_1m_usdt = 0
                change_1m = 0
                natr = 0
            else:
                # Получаем объём за 1 минуту из klines
                last_candle = klines_data[-1] if klines_data else None
                if last_candle:
                    volume_1m_usdt = float(last_candle[7])  # quoteAssetVolume - оборот в USDT
                    open_price = float(last_candle[1])
                    close_price = float(last_candle[4])
                    high_price = float(last_candle[2])
                    low_price = float(last_candle[3])

                    # Рассчитываем изменение за 1 минуту
                    if open_price > 0:
                        change_1m = ((close_price - open_price) / open_price) * 100
                    else:
                        change_1m = 0

                    # Рассчитываем NATR за 1 минуту
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

            bot_logger.info(f"{symbol}: 1m volume={volume_1m_usdt:.2f} USDT из klines")

            # Получаем количество сделок за 1 минуту
            trades_count = 0
            if not isinstance(trades_1m, Exception) and isinstance(trades_1m, int):
                trades_count = trades_1m

            bot_logger.info(f"{symbol}: Точные данные - volume={volume_1m_usdt:.2f} USDT, trades={trades_count}")

            # Рассчитываем спред (стандартная формула относительно bid цены)
            bid_price = float(book_data['bidPrice'])
            ask_price = float(book_data['askPrice'])
            spread = ((ask_price - bid_price) / bid_price) * 100 if bid_price > 0 else 0

            # Проверяем активность только по 1-минутным данным
            vol_thresh = config_manager.get('VOLUME_THRESHOLD')
            spread_thresh = config_manager.get('SPREAD_THRESHOLD')
            natr_thresh = config_manager.get('NATR_THRESHOLD')

            is_active = (
                volume_1m_usdt >= vol_thresh and
                spread >= spread_thresh and
                natr >= natr_thresh
            )

            # Проверяем наличие недавних сделок (за последние 60 секунд)
            has_recent_trades = trades_count > 0

            price = float(ticker_data['lastPrice']) if ticker_data else 0

            coin_data = {
                'symbol': symbol,
                'price': price,
                'volume': volume_1m_usdt,  # 1-минутный оборот в USDT
                'change': change_1m,  # 1-минутное изменение
                'spread': spread,
                'natr': natr,  # 1-минутный NATR
                'trades': trades_count,  # Количество сделок за 1 минуту
                'active': is_active,
                'has_recent_trades': has_recent_trades,
                'timestamp': time.time()
            }
            
            # Валидируем данные перед возвратом
            if not data_validator.validate_coin_data(coin_data):
                bot_logger.warning(f"Данные для {symbol} не прошли валидацию")
                return None
                
            return coin_data

        except asyncio.CancelledError:
            bot_logger.debug(f"Запрос для {symbol} был отменен")
            return None
        except Exception as e:
            bot_logger.error(f"Ошибка получения данных для {symbol}: {e}")
            return None

    async def _rate_limit(self):
        """Реализует rate limiting"""
        interval = time.time() - self.last_request_time
        if interval < 0.1:
            await asyncio.sleep(0.1 - interval)
        self.last_request_time = time.time()

    async def get_current_price_fast(self, symbol: str) -> Optional[float]:
        """Быстрое получение текущей цены монеты с кешированием"""
        try:
            # Проверяем кеш цены
            cached_price = cache_manager.get_price_cache(symbol)
            if cached_price:
                return cached_price
                
            ticker_data = await self.get_ticker_data(symbol)
            if ticker_data and 'lastPrice' in ticker_data:
                price = float(ticker_data['lastPrice'])
                cache_manager.set_price_cache(symbol, price)
                return price
            return None
        except Exception as e:
            bot_logger.debug(f"Ошибка получения цены {symbol}: {e}")
            return None

    async def close(self):
        """Правильно закрывает HTTP сессию и коннекторы"""
        if self.session and not self.session.closed:
            try:
                # Закрываем сессию
                await self.session.close()

                # Даем время на завершение всех соединений
                await asyncio.sleep(0.25)

                bot_logger.debug("HTTP сессия корректно закрыта")
            except Exception as e:
                bot_logger.debug(f"Ошибка при закрытии HTTP сессии: {type(e).__name__}")
            finally:
                self.session = None

# Глобальный экземпляр
api_client = APIClient()