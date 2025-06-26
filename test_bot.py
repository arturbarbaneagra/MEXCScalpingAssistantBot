
import unittest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from data_validator import data_validator
from circuit_breaker import CircuitBreaker, CircuitState
from cache_manager import CacheManager
from config import ConfigManager

class TestDataValidator(unittest.TestCase):
    """Тесты для валидатора данных"""
    
    def test_validate_symbol(self):
        """Тест валидации символов"""
        # Корректные символы
        self.assertTrue(data_validator.validate_symbol("BTC"))
        self.assertTrue(data_validator.validate_symbol("BTCUSDT"))
        self.assertTrue(data_validator.validate_symbol("BTC_USDT"))
        self.assertTrue(data_validator.validate_symbol("ATOM1"))
        
        # Некорректные символы
        self.assertFalse(data_validator.validate_symbol(""))
        self.assertFalse(data_validator.validate_symbol("A"))
        self.assertFalse(data_validator.validate_symbol("VERYLONGSYMBOL"))
        self.assertFalse(data_validator.validate_symbol("BTC@#$"))
        self.assertFalse(data_validator.validate_symbol(None))

    def test_validate_coin_data(self):
        """Тест валидации данных монеты"""
        # Корректные данные
        valid_data = {
            'symbol': 'BTC',
            'price': 50000.0,
            'volume': 1000.0,
            'change': 2.5,
            'spread': 0.1,
            'natr': 0.5,
            'trades': 10,
            'active': True
        }
        self.assertTrue(data_validator.validate_coin_data(valid_data))
        
        # Некорректные данные
        invalid_data = valid_data.copy()
        invalid_data['price'] = -100  # Отрицательная цена
        self.assertFalse(data_validator.validate_coin_data(invalid_data))
        
        # Отсутствующее поле
        incomplete_data = valid_data.copy()
        del incomplete_data['symbol']
        self.assertFalse(data_validator.validate_coin_data(incomplete_data))

    def test_sanitize_user_input(self):
        """Тест очистки пользовательского ввода"""
        self.assertEqual(data_validator.sanitize_user_input("  BTC  "), "BTC")
        self.assertEqual(data_validator.sanitize_user_input("BTC<script>"), "BTCscript")
        self.assertEqual(data_validator.sanitize_user_input("BTC\n\tETH"), "BTCETH")

class TestCircuitBreaker(unittest.IsolatedAsyncioTestCase):
    """Тесты для Circuit Breaker"""
    
    async def test_circuit_breaker_normal_operation(self):
        """Тест нормальной работы Circuit Breaker"""
        cb = CircuitBreaker(failure_threshold=2, name="test")
        
        # Успешный вызов
        async def success_func():
            return "success"
        
        result = await cb.call(success_func)
        self.assertEqual(result, "success")
        self.assertEqual(cb.state, CircuitState.CLOSED)

    async def test_circuit_breaker_opens_on_failures(self):
        """Тест открытия Circuit Breaker при ошибках"""
        cb = CircuitBreaker(failure_threshold=2, name="test")
        
        async def failing_func():
            raise Exception("Test error")
        
        # Первая ошибка
        with self.assertRaises(Exception):
            await cb.call(failing_func)
        self.assertEqual(cb.state, CircuitState.CLOSED)
        
        # Вторая ошибка - должен открыться
        with self.assertRaises(Exception):
            await cb.call(failing_func)
        self.assertEqual(cb.state, CircuitState.OPEN)

class TestCacheManager(unittest.TestCase):
    """Тесты для менеджера кеша"""
    
    def setUp(self):
        self.cache = CacheManager()
    
    def test_cache_set_get(self):
        """Тест установки и получения данных из кеша"""
        self.cache.set("test_key", "test_value")
        self.assertEqual(self.cache.get("test_key"), "test_value")
    
    def test_cache_expiration(self):
        """Тест истечения кеша"""
        import time
        self.cache.set("test_key", "test_value")
        
        # Имитируем истечение времени
        self.cache.cache["test_key"] = ("test_value", time.time() - 100)
        
        self.assertIsNone(self.cache.get("test_key", ttl=50))

    def test_ticker_cache(self):
        """Тест кеша тикеров"""
        test_data = {'symbol': 'BTCUSDT', 'price': '50000'}
        self.cache.set_ticker_cache("BTC", test_data)
        
        cached_data = self.cache.get_ticker_cache("BTC")
        self.assertEqual(cached_data, test_data)

class TestConfigManager(unittest.TestCase):
    """Тесты для менеджера конфигурации"""
    
    def setUp(self):
        self.config = ConfigManager("test_config.json")
    
    def test_config_defaults(self):
        """Тест дефолтных значений конфигурации"""
        self.assertIsNotNone(self.config.get('VOLUME_THRESHOLD'))
        self.assertIsNotNone(self.config.get('SPREAD_THRESHOLD'))
        self.assertIsNotNone(self.config.get('NATR_THRESHOLD'))
    
    def test_config_set_get(self):
        """Тест установки и получения конфигурации"""
        self.config.set('TEST_VALUE', 123)
        self.assertEqual(self.config.get('TEST_VALUE'), 123)
    
    def tearDown(self):
        """Очистка тестовых файлов"""
        import os
        if os.path.exists("test_config.json"):
            os.remove("test_config.json")

if __name__ == '__main__':
    # Запуск тестов
    unittest.main(verbosity=2)
