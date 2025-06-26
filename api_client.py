import asyncio
import time
import aiohttp
from typing import Optional, Dict, List
from logger import bot_logger
from config import config_manager

class OptimizedMexcApiClient:
    def __init__(self):
        self.base_url = "https://api.mexc.com/api/v3"
        self.session = None
        self.cache = {}
        self.cache_ttl = {}
        self.last_request_time = 0
        self.request_count = 0
        self.rate_limit_window = 1.0
        self.adaptive_delay = 0.1  # Адаптивная задержка
        self.consecutive_errors = 0

        # Кэш настройки
        self.price_cache_duration = 5  # 5 секунд для цен (скальпинг)
        self.candle_cache_duration = 10  # 10 секунд для свечей
        self.ticker_cache_duration = 3  # 3 секунды для тикеров

    async def _ensure_session(self):
        """Обеспечивает наличие активной сессии"""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=config_manager.get('API_TIMEOUT', 15))
            connector = aiohttp.TCPConnector(
                limit=50,  # Максимум соединений в пуле
                limit_per_host=20,  # На хост
                ttl_dns_cache=300,  # DNS кэш на 5 минут
                use_dns_cache=True,
                keepalive_timeout=60,
                enable_cleanup_closed=True
            )

            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    'User-Agent': 'ScalpingBot/3.0',
                    'Accept': 'application/json',
                    'Connection': 'keep-alive'
                }
            )
            bot_logger.info("🔄 Новая оптимизированная HTTP сессия создана")

    async def _adaptive_rate_limit(self):
        """Адаптивное ограничение скорости запросов"""
        current_time = time.time()

        # Сброс счетчика каждую секунду
        if current_time - self.last_request_time >= self.rate_limit_window:
            self.request_count = 0
            self.last_request_time = current_time
            # Уменьшаем задержку при успешных запросах
            if self.consecutive_errors == 0:
                self.adaptive_delay = max(0.05, self.adaptive_delay * 0.95)

        # Проверяем лимит запросов
        max_requests = config_manager.get('MAX_API_REQUESTS_PER_SECOND', 6)
        if self.request_count >= max_requests:
            sleep_time = self.rate_limit_window - (current_time - self.last_request_time)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
                self.request_count = 0
                self.last_request_time = time.time()

        # Адаптивная задержка между запросами
        if self.request_count > 0:
            await asyncio.sleep(self.adaptive_delay)

        self.request_count += 1

    def _get_cache_key(self, endpoint: str, params: Dict = None) -> str:
        """Генерирует ключ кэша"""
        if params:
            param_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            return f"{endpoint}?{param_str}"
        return endpoint

    def _is_cache_valid(self, cache_key: str, ttl_seconds: int) -> bool:
        """Проверяет валидность кэша"""
        if cache_key not in self.cache or cache_key not in self.cache_ttl:
            return False
        return time.time() - self.cache_ttl[cache_key] < ttl_seconds

    def _set_cache(self, cache_key: str, data: any):
        """Сохраняет данные в кэш"""
        self.cache[cache_key] = data
        self.cache_ttl[cache_key] = time.time()

        # Очистка старого кэша (простая стратегия)
        if len(self.cache) > 1000:  # Максимум 1000 записей
            oldest_keys = sorted(self.cache_ttl.keys(), key=lambda k: self.cache_ttl[k])[:100]
            for key in oldest_keys:
                self.cache.pop(key, None)
                self.cache_ttl.pop(key, None)

    async def _make_request(self, endpoint: str, params: Dict = None, cache_ttl: int = 0) -> Optional[Dict]:
        """Выполняет HTTP запрос с кэшированием и повторными попытками"""
        await self._ensure_session()

        # Проверяем кэш
        cache_key = self._get_cache_key(endpoint, params)
        if cache_ttl > 0 and self._is_cache_valid(cache_key, cache_ttl):
            return self.cache[cache_key]

        url = f"{self.base_url}/{endpoint}"
        max_retries = config_manager.get('MAX_RETRIES', 3)

        for attempt in range(max_retries):
            try:
                await self._adaptive_rate_limit()

                start_time = time.time()
                async with self.session.get(url, params=params) as response:
                    response_time = time.time() - start_time

                    # Логируем без чувствительных данных
                    safe_url = url.split('?')[0].replace('https://api.mexc.com', 'MEXC_API')
                    bot_logger.api_request('GET', safe_url, response.status, response_time)

                    if response.status == 200:
                        data = await response.json()
                        # Сохраняем в кэш если нужно
                        if cache_ttl > 0:
                            self._set_cache(cache_key, data)
                        self.consecutive_errors = 0
                        return data

                    elif response.status == 429:  # Rate limit
                        self.consecutive_errors += 1
                        wait_time = min(2 ** attempt, 10)  # Максимум 10 секунд
                        self.adaptive_delay = min(self.adaptive_delay * 1.5, 2.0)  # Увеличиваем задержку
                        bot_logger.warning(f"Rate limit hit, waiting {wait_time}s")
                        await asyncio.sleep(wait_time)
                        continue

                    else:
                        self.consecutive_errors += 1
                        bot_logger.error(f"API error {response.status}: {await response.text()}")
                        if attempt == max_retries - 1:
                            return None

            except asyncio.TimeoutError:
                self.consecutive_errors += 1
                bot_logger.warning(f"Request timeout on attempt {attempt + 1}")
                if attempt == max_retries - 1:
                    return None
                await asyncio.sleep(2 ** attempt)

            except Exception as e:
                self.consecutive_errors += 1
                bot_logger.error(f"Request exception on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    return None
                await asyncio.sleep(2 ** attempt)

        return None

    async def get_multiple_tickers_batch(self, symbols: List[str]) -> Dict[str, Optional[Dict]]:
        """Получает данные для нескольких символов параллельно (оптимизированно)"""
        if not symbols:
            return {}

        # Создаем задачи для параллельного выполнения
        tasks = []
        for symbol in symbols:
            task = asyncio.create_task(self.get_optimized_ticker(symbol))
            tasks.append((symbol, task))

        results = {}

        # Выполняем запросы батчами для контроля нагрузки
        batch_size = config_manager.get('CHECK_BATCH_SIZE', 8)
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]

            for symbol, task in batch:
                try:
                    result = await task
                    results[symbol] = result
                except Exception as e:
                    bot_logger.error(f"Ошибка получения данных для {symbol}: {e}")
                    results[symbol] = None

            # Небольшая пауза между батчами
            if i + batch_size < len(tasks):
                await asyncio.sleep(config_manager.get('CHECK_BATCH_INTERVAL', 1.0))

        return results

    async def get_optimized_ticker(self, symbol: str) -> Optional[Dict]:
        """Оптимизированное получение тикера для скальпинга"""
        symbol = symbol if symbol.endswith("USDT") else symbol + "USDT"

        # Валидация символа
        if not symbol or len(symbol.replace("USDT", "")) < 2:
            return None

        try:
            # Параллельно получаем свечи и текущие цены
            candle_task = self.get_fast_candle(symbol)
            price_task = self.get_current_price_fast(symbol)

            candle_data, current_price_data = await asyncio.gather(
                candle_task, price_task, return_exceptions=True
            )

            # Обрабатываем результаты
            if isinstance(candle_data, Exception) or not candle_data:
                bot_logger.debug(f"Нет данных свечей для {symbol}")
                return None

            if isinstance(current_price_data, Exception) or not current_price_data:
                bot_logger.debug(f"Нет текущих цен для {symbol}")
                return None

            # Извлекаем данные из свечей (последние 2 завершенные минуты)
            candles = candle_data.get('klines', [])
            if len(candles) < 2:
                return None

            # Берем две последние завершенные свечи
            prev_candle = candles[-2]
            curr_candle = candles[-1]

            current_close = float(curr_candle['close'])
            previous_close = float(prev_candle['close'])
            current_volume = float(curr_candle['volume'])

            # Рассчитываем 1-минутное изменение
            price_change = ((current_close - previous_close) / previous_close * 100) if previous_close > 0 else 0.0

            # Объем в USDT
            volume_usdt = current_volume * current_close

            # Получаем bid/ask из данных о текущих ценах
            bid_price = current_price_data.get('bidPrice', current_close)
            ask_price = current_price_data.get('askPrice', current_close)

            if isinstance(bid_price, str):
                bid_price = float(bid_price)
            if isinstance(ask_price, str):
                ask_price = float(ask_price)

            return {
                'price': current_close,
                'change': price_change,
                'volume': volume_usdt,
                'count': 0,  # Упрощаем для скорости
                'bid': bid_price,
                'ask': ask_price,
                'timestamp': time.time()
            }

        except Exception as e:
            bot_logger.warning(f"Ошибка обработки оптимизированного тикера для {symbol}: {e}")
            return None

    async def get_fast_candle(self, symbol: str) -> Optional[Dict]:
        """Быстрое получение свечей с кэшированием"""
        params = {
            'symbol': symbol,
            'interval': '1m',
            'limit': 3  # Получаем 3 последние свечи для надежности
        }

        data = await self._make_request('klines', params, cache_ttl=self.candle_cache_duration)
        if not data or not isinstance(data, list):
            return None

        try:
            klines = []
            for item in data:
                if len(item) >= 8:
                    kline = {
                        'open_time': int(item[0]),
                        'open': float(item[1]),
                        'high': float(item[2]),
                        'low': float(item[3]),
                        'close': float(item[4]),
                        'volume': float(item[5]),
                        'close_time': int(item[6]),
                        'quote_volume': float(item[7])
                    }
                    klines.append(kline)

            return {'klines': klines} if klines else None

        except (ValueError, IndexError) as e:
            bot_logger.warning(f"Ошибка парсинга быстрых свечей для {symbol}: {e}")
            return None

    async def get_current_price_fast(self, symbol: str) -> Optional[Dict]:
        """Быстрое получение текущих цен bid/ask"""
        params = {'symbol': symbol}

        return await self._make_request(
            'ticker/bookTicker', 
            params, 
            cache_ttl=self.price_cache_duration
        )

    async def get_coin_data(self, symbol: str) -> Optional[Dict]:
        """Получает полные данные монеты (оптимизированная версия)"""
        try:
            ticker_data = await self.get_optimized_ticker(symbol)
            if not ticker_data:
                return None

            # Рассчитываем метрики
            volume = ticker_data['volume']
            spread = self._calculate_spread(ticker_data['bid'], ticker_data['ask'])

            # Упрощенный NATR на основе текущих данных
            natr = spread * 0.5  # Приблизительная оценка для скорости

            # Проверяем критерии активности
            volume_threshold = config_manager.get('VOLUME_THRESHOLD')
            spread_threshold = config_manager.get('SPREAD_THRESHOLD')
            natr_threshold = config_manager.get('NATR_THRESHOLD')

            is_active = (
                volume >= volume_threshold and
                spread >= spread_threshold and
                natr >= natr_threshold
            )

            return {
                'symbol': symbol,
                'price': ticker_data['price'],
                'change': ticker_data['change'],
                'volume': volume,
                'trades': ticker_data['count'],
                'spread': spread,
                'natr': natr,
                'active': is_active,
                'timestamp': time.time()
            }

        except Exception as e:
            bot_logger.error(f"Ошибка получения данных для {symbol}: {e}")
            return None

    def _calculate_spread(self, bid: float, ask: float) -> float:
        """Рассчитывает спред в процентах"""
        if bid <= 0 or ask <= 0:
            return 0.0
        return ((ask - bid) / bid) * 100

    async def close(self):
        """Закрывает сессию"""
        if self.session and not self.session.closed:
            await self.session.close()
            bot_logger.info("🔒 HTTP сессия закрыта")

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

# Глобальный экземпляр оптимизированного API клиента
api_client = OptimizedMexcApiClient()