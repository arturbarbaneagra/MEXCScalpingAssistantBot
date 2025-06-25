import time
import requests
from typing import Optional, Dict
from logger import bot_logger
from config import config_manager

class MexcApiClient:
    def __init__(self):
        self.base_url = "https://api.mexc.com/api/v3"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'TradingBot/2.0',
            'Content-Type': 'application/json'
        })
        self.last_request_time = 0
        self.request_count = 0
        self.rate_limit_window = 1.0  # 1 секунда

    def _rate_limit(self):
        """Ограничение скорости API запросов"""
        current_time = time.time()

        # Сброс счетчика каждую секунду
        if current_time - self.last_request_time >= self.rate_limit_window:
            self.request_count = 0
            self.last_request_time = current_time

        # Ограничиваем количество запросов в секунду
        max_requests = config_manager.get('MAX_API_REQUESTS_PER_SECOND')
        if self.request_count >= max_requests:
            sleep_time = self.rate_limit_window - (current_time - self.last_request_time)
            if sleep_time > 0:
                time.sleep(sleep_time)
                self.request_count = 0
                self.last_request_time = time.time()

        self.request_count += 1

    def _make_request(self, endpoint: str, params: Dict = None, timeout: int = None) -> Optional[Dict]:
        """Выполняет HTTP запрос с повторными попытками"""
        if timeout is None:
            timeout = config_manager.get('API_TIMEOUT')

        max_retries = config_manager.get('MAX_RETRIES')
        url = f"{self.base_url}/{endpoint}"

        for attempt in range(max_retries):
            try:
                self._rate_limit()

                start_time = time.time()
                response = self.session.get(url, params=params, timeout=timeout)
                response_time = time.time() - start_time

                bot_logger.api_request('GET', url, response.status_code, response_time)

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:  # Rate limit
                    wait_time = 2 ** attempt
                    bot_logger.warning(f"Rate limit hit, waiting {wait_time}s")
                    time.sleep(wait_time)
                    continue
                else:
                    bot_logger.error(f"API error {response.status_code}: {response.text}")
                    if attempt == max_retries - 1:
                        return None

            except requests.exceptions.RequestException as e:
                bot_logger.error(f"Request exception on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    return None
                time.sleep(2 ** attempt)

        return None

    def get_candle(self, symbol: str, interval: str = '1m') -> Optional[Dict]:
        """Получает данные свечи согласно документации MEXC"""
        symbol = symbol if symbol.endswith("USDT") else symbol + "USDT"
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': 1
        }

        data = self._make_request('klines', params)
        if not data or not isinstance(data, list) or len(data) == 0:
            return None

        try:
            candle = data[0]
            # Структура согласно MEXC API документации:
            # [Open time, Open, High, Low, Close, Volume, Close time, Quote asset volume]
            if len(candle) < 8:
                bot_logger.warning(f"Некорректная структура свечи для {symbol}: {len(candle)} полей")
                return None

            return {
                'open_time': int(candle[0]),      # Index 0: Open time
                'open': float(candle[1]),         # Index 1: Open
                'high': float(candle[2]),         # Index 2: High
                'low': float(candle[3]),          # Index 3: Low  
                'close': float(candle[4]),        # Index 4: Close
                'volume': float(candle[5]),       # Index 5: Volume
                'close_time': int(candle[6]),     # Index 6: Close time
                'quote_volume': float(candle[7]), # Index 7: Quote asset volume
                'timestamp': int(candle[0])       # Для обратной совместимости
            }
        except (IndexError, ValueError, TypeError) as e:
            bot_logger.error(f"Ошибка парсинга свечи для {symbol}: {e}")
            return None

    def get_ticker(self, symbol: str) -> Optional[Dict]:
        """Получает тикер монеты с 1-минутными данными согласно MEXC API"""
        symbol = symbol if symbol.endswith("USDT") else symbol + "USDT"

        # Получаем последние 3 завершенные 1-минутные свечи согласно документации MEXC
        candle_params = {
            'symbol': symbol,
            'interval': '1m',
            'limit': 3
        }

        candle_data = self._make_request('klines', candle_params)
        if not candle_data or not isinstance(candle_data, list) or len(candle_data) < 2:
            bot_logger.warning(f"Недостаточно 1-минутных данных для {symbol}")
            return None

        try:
            # Сортируем по времени (самая новая свеча - последняя)
            candle_data.sort(key=lambda x: int(x[0]))

            # Берем две последние завершенные свечи
            previous_candle = candle_data[-2]  # Предыдущая минута
            current_candle = candle_data[-1]   # Последняя завершенная минута

            # MEXC API структура: [Open time, Open, High, Low, Close, Volume, Close time, Quote asset volume]
            if not isinstance(current_candle, list) or len(current_candle) < 8:
                bot_logger.warning(f"Некорректная структура свечи для {symbol}: ожидается 8 полей, получено {len(current_candle) if isinstance(current_candle, list) else 'не массив'}")
                return None

            # Согласно документации MEXC:
            # Index 0: Open time
            # Index 1: Open  
            # Index 2: High
            # Index 3: Low
            # Index 4: Close
            # Index 5: Volume
            # Index 6: Close time
            # Index 7: Quote asset volume

            current_close = float(current_candle[4])     # Close price
            current_volume = float(current_candle[5])    # Volume
            quote_volume = float(current_candle[7])      # Quote asset volume (для расчета количества сделок)

            # Данные предыдущей свечи для расчета изменения
            previous_close = float(previous_candle[4])

            # Рассчитываем изменение цены за последнюю минуту
            price_change = ((current_close - previous_close) / previous_close * 100) if previous_close > 0 else 0.0

            # Получаем текущие bid/ask цены из orderbook
            ticker_params = {'symbol': symbol}
            ticker_data = self._make_request('ticker/bookTicker', ticker_params)

            bid_price = float(ticker_data['bidPrice']) if ticker_data and ticker_data.get('bidPrice') else current_close
            ask_price = float(ticker_data['askPrice']) if ticker_data and ticker_data.get('askPrice') else current_close

            # Получаем количество сделок из 24hr ticker
            count_data = self._make_request('ticker/24hr', {'symbol': symbol})
            trade_count = int(count_data.get('count', 0)) if count_data else 0

            bot_logger.debug(f"{symbol}: price={current_close:.6f}, 1m_change={price_change:+.2f}%, volume=${current_volume:,.0f}, quote_vol=${quote_volume:,.0f}")

            return {
                'price': current_close,
                'change': price_change,  # 1-минутное изменение
                'volume': current_volume * current_close,  # Объем в USDT
                'count': trade_count,  # Количество сделок за 24ч (для скальпинга)
                'bid': bid_price,
                'ask': ask_price
            }
        except (KeyError, ValueError, TypeError, IndexError) as e:
            bot_logger.warning(f"Ошибка обработки 1-минутных данных для {symbol}: {e}")
            return None

    def get_coin_data(self, symbol: str) -> Optional[Dict]:
        """Получает полные данные монеты"""
        try:
            # Получаем данные тикера и свечи параллельно
            ticker_data = self.get_ticker(symbol)
            if not ticker_data:
                return None

            candle_data = self.get_candle(symbol)
            if not candle_data:
                # Если свеча недоступна, используем данные из тикера
                candle_data = {
                    'high': ticker_data['price'],
                    'low': ticker_data['price'],
                    'close': ticker_data['price']
                }

            # Рассчитываем метрики
            volume = ticker_data['volume']
            spread = self._calculate_spread(ticker_data['bid'], ticker_data['ask'])
            natr = self._calculate_natr(candle_data['high'], candle_data['low'], candle_data['close'])

            # Проверяем критерии активности
            volume_threshold = config_manager.get('VOLUME_THRESHOLD')
            spread_threshold = config_manager.get('SPREAD_THRESHOLD')
            natr_threshold = config_manager.get('NATR_THRESHOLD')

            is_active = (
                volume >= volume_threshold and
                spread >= spread_threshold and
                natr >= natr_threshold
            )

            result = {
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

            return result

        except Exception as e:
            bot_logger.error(f"Ошибка получения данных для {symbol}: {e}")
            return None

    def _calculate_spread(self, bid: float, ask: float) -> float:
        """Рассчитывает спред в процентах"""
        if bid <= 0 or ask <= 0:
            return 0.0
        return ((ask - bid) / bid) * 100

    def _calculate_natr(self, high: float, low: float, close: float) -> float:
        """Рассчитывает нормализованный ATR"""
        if close <= 0:
            return 0.0
        true_range = high - low
        return (true_range / close) * 100

    def get_klines(self, symbol: str, interval: str = "1m", limit: int = 100) -> Optional[Dict]:
        """Получает данные свечей для символа (1-минутные данные для скальпинга)"""
        try:
            endpoint = "/api/v3/klines"
            params = {
                'symbol': symbol,
                'interval': interval,
                'limit': limit
            }

            url = f"{self.base_url}{endpoint}"
            start_time = time.time()

            response = requests.get(
                url,
                params=params,
                timeout=config_manager.get('API_TIMEOUT', 15)
            )

            response_time = time.time() - start_time

            # Безопасное логирование без чувствительных данных
            safe_url = url.split('?')[0]  # Убираем параметры из URL
            bot_logger.api_request("GET", safe_url, response.status_code, response_time)

            if response.status_code == 200:
                data = response.json()
                if data:
                    # Форматируем данные свечи согласно документации MEXC
                    klines = []
                    for item in data:
                        kline = {
                            'open_time': item[0],           # Index 0: Open time
                            'open': float(item[1]),         # Index 1: Open
                            'high': float(item[2]),         # Index 2: High
                            'low': float(item[3]),          # Index 3: Low
                            'close': float(item[4]),        # Index 4: Close
                            'volume': float(item[5]),       # Index 5: Volume
                            'close_time': item[6],          # Index 6: Close time
                            'quote_volume': float(item[7])  # Index 7: Quote asset volume
                        }
                        klines.append(kline)

                    return {'klines': klines}

            return None

        except Exception as e:
            bot_logger.error(f"Ошибка получения klines для {symbol}: {e}")
            return None

    def get_ticker_24hr(self, symbol: str = None) -> Optional[Dict]:
        """Получает статистику за 24 часа (используется для получения базовой информации)"""
        try:
            endpoint = "/api/v3/ticker/24hr"
            params = {}
            if symbol:
                params['symbol'] = symbol

            url = f"{self.base_url}{endpoint}"
            start_time = time.time()

            response = requests.get(
                url,
                params=params,
                timeout=config_manager.get('API_TIMEOUT', 15)
            )

            response_time = time.time() - start_time

            # Безопасное логирование
            safe_url = url.split('?')[0]
            bot_logger.api_request("GET", safe_url, response.status_code, response_time)

            if response.status_code == 200:
                return response.json()

            return None

        except Exception as e:
            bot_logger.error(f"Ошибка получения ticker 24hr: {e}")
            return None

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Получает текущую цену для символа"""
        try:
            endpoint = "/api/v3/ticker/price"
            params = {'symbol': symbol}

            url = f"{self.base_url}{endpoint}"
            start_time = time.time()

            response = requests.get(
                url,
                params=params,
                timeout=config_manager.get('API_TIMEOUT', 15)
            )

            response_time = time.time() - start_time

            # Безопасное логирование
            safe_url = url.split('?')[0]
            bot_logger.api_request("GET", safe_url, response.status_code, response_time)

            if response.status_code == 200:
                data = response.json()
                return float(data.get('price', 0))

            return None

        except Exception as e:
            bot_logger.error(f"Ошибка получения цены для {symbol}: {e}")
            return None

# Глобальный экземпляр API клиента
api_client = MexcApiClient()