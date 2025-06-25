
import requests
import time
from typing import Optional, Dict, Any, List
from logger import bot_logger
from config import config_manager
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed

class MexcApiClient:
    def __init__(self):
        self.base_url = "https://api.mexc.com/api/v3"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'TradingBot/1.0'
        })
        self.last_request_time = 0
        self.request_count = 0
        self.rate_limit_window = 1.0  # 1 секунда
        
    def _rate_limit(self):
        """Ограничение частоты запросов"""
        current_time = time.time()
        if current_time - self.last_request_time < self.rate_limit_window:
            if self.request_count >= config_manager.get('MAX_API_REQUESTS_PER_SECOND'):
                sleep_time = self.rate_limit_window - (current_time - self.last_request_time)
                time.sleep(sleep_time)
                self.request_count = 0
                self.last_request_time = time.time()
        else:
            self.request_count = 0
            self.last_request_time = current_time
        
        self.request_count += 1
    
    def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Базовый метод для выполнения запросов с повторными попытками"""
        self._rate_limit()
        
        url = f"{self.base_url}/{endpoint}"
        max_retries = config_manager.get('MAX_RETRIES')
        timeout = config_manager.get('API_TIMEOUT')
        
        for attempt in range(max_retries):
            try:
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
        """Получает данные свечи"""
        symbol = symbol if symbol.endswith("USDT") else symbol + "USDT"
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': 1
        }
        
        data = self._make_request('klines', params)
        if not data or not isinstance(data, list) or not data:
            return None
        
        try:
            candle = data[0]
            return {
                'symbol': symbol,
                'open': float(candle[1]),
                'high': float(candle[2]),
                'low': float(candle[3]),
                'close': float(candle[4]),
                'volume': float(candle[7]),
                'timestamp': int(candle[0])
            }
        except (IndexError, ValueError) as e:
            bot_logger.error(f"Error parsing candle data for {symbol}: {e}")
            return None
    
    def get_depth(self, symbol: str) -> Optional[float]:
        """Получает спред"""
        symbol = symbol if symbol.endswith("USDT") else symbol + "USDT"
        params = {'symbol': symbol, 'limit': 1}
        
        data = self._make_request('depth', params)
        if not data:
            return None
        
        try:
            bids = data.get('bids', [])
            asks = data.get('asks', [])
            
            if not bids or not asks:
                return None
            
            bid_price = float(bids[0][0])
            ask_price = float(asks[0][0])
            
            if bid_price <= 0:
                return None
            
            spread = (ask_price - bid_price) / bid_price * 100
            return spread
            
        except (IndexError, ValueError, ZeroDivisionError) as e:
            bot_logger.error(f"Error calculating spread for {symbol}: {e}")
            return None
    
    def get_trade_count(self, symbol: str) -> int:
        """Получает количество сделок за последнюю минуту"""
        symbol = symbol if symbol.endswith("USDT") else symbol + "USDT"
        params = {'symbol': symbol, 'limit': 1000}
        
        data = self._make_request('trades', params)
        if not data:
            return 0
        
        try:
            one_minute_ago = int(time.time() * 1000) - 60_000
            recent_trades = [t for t in data if t.get('time', 0) >= one_minute_ago]
            return len(recent_trades)
        except Exception as e:
            bot_logger.error(f"Error counting trades for {symbol}: {e}")
            return 0
    
    def get_coin_data(self, symbol: str) -> Optional[Dict]:
        """Получает полные данные по монете"""
        try:
            # Добавляем задержку между запросами
            time.sleep(config_manager.get('COIN_DATA_DELAY'))
            
            candle = self.get_candle(symbol)
            if not candle:
                return None
            
            spread = self.get_depth(symbol)
            if spread is None:
                spread = 0.0
            
            trades = self.get_trade_count(symbol)
            
            # Вычисляем NATR
            natr = 0.0
            if candle['close'] > 0:
                natr = (candle['high'] - candle['low']) / candle['close'] * 100
            
            # Вычисляем изменение цены
            change = 0.0
            if candle['open'] > 0:
                change = (candle['close'] - candle['open']) / candle['open'] * 100
            
            # Определяем активность
            is_active = (
                candle['volume'] >= config_manager.get('VOLUME_THRESHOLD') and
                spread >= config_manager.get('SPREAD_THRESHOLD') and
                natr >= config_manager.get('NATR_THRESHOLD')
            )
            
            result = {
                'symbol': symbol.replace('USDT', ''),
                'volume': candle['volume'],
                'spread': spread,
                'natr': natr,
                'change': change,
                'trades': trades,
                'price': candle['close'],
                'active': is_active,
                'timestamp': candle['timestamp']
            }
            
            bot_logger.info(f"Data for {symbol}: Active={is_active}, Volume=${candle['volume']:,.2f}")
            return result
            
        except Exception as e:
            bot_logger.error(f"Error getting coin data for {symbol}: {e}", exc_info=True)
            return None

# Глобальный экземпляр API клиента
api_client = MexcApiClient()
