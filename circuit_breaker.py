import time
import asyncio
from enum import Enum
from typing import Callable, Any, Optional
from logger import bot_logger

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        timeout: float = 60.0,
        recovery_timeout: float = 30.0,
        name: str = "circuit_breaker"
    ):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.recovery_timeout = recovery_timeout
        self.name = name

        self.failure_count = 0
        self.last_failure_time = 0
        self.state = CircuitState.CLOSED

        bot_logger.debug(f"Инициализирован Circuit Breaker '{name}'")

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Выполняет функцию через Circuit Breaker"""

        # Проверяем состояние
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                bot_logger.info(f"Circuit Breaker '{self.name}' переключен в HALF_OPEN")
            else:
                raise Exception(f"Circuit Breaker '{self.name}' OPEN - вызов заблокирован")

        try:
            # Выполняем функцию
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            # Успешное выполнение
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                bot_logger.info(f"Circuit Breaker '{self.name}' восстановлен - CLOSED")

            return result

        except Exception as e:
            # Не считаем валидационные ошибки как failures
            if isinstance(e, ValueError) and "Invalid symbol" in str(e):
                bot_logger.debug(f"Circuit Breaker '{self.name}': пропускаем валидационную ошибку")
                raise e
            
            self.failure_count += 1
            self.last_failure_time = time.time()

            # Проверяем превышение порога ошибок
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                bot_logger.warning(
                    f"Circuit Breaker '{self.name}' сработал - OPEN "
                    f"(ошибок: {self.failure_count}/{self.failure_threshold})"
                )

            raise e

    def get_stats(self) -> dict:
        """Возвращает статистику Circuit Breaker"""
        return {
            'name': self.name,
            'state': self.state.value,
            'failure_count': self.failure_count,
            'failure_threshold': self.failure_threshold,
            'last_failure_time': self.last_failure_time
        }

    def reset(self):
        """Сбрасывает Circuit Breaker"""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0
        bot_logger.info(f"Circuit Breaker '{self.name}' сброшен")

    def force_close(self):
        """Принудительно закрывает Circuit Breaker при успешных операциях"""
        if self.state in [CircuitState.HALF_OPEN, CircuitState.OPEN]:
            self.state = CircuitState.CLOSED
            self.failure_count = max(0, self.failure_count - 2)  # Уменьшаем счетчик
            bot_logger.info(f"Circuit Breaker '{self.name}' принудительно закрыт")

# Глобальные Circuit Breaker для разных API endpoint'ов с более мягкими параметрами
api_circuit_breakers = {
    'ticker': CircuitBreaker(failure_threshold=8, timeout=30, recovery_timeout=15, name='ticker_api'),
    'klines': CircuitBreaker(failure_threshold=8, timeout=30, recovery_timeout=15, name='klines_api'),
    'trades': CircuitBreaker(failure_threshold=10, timeout=60, recovery_timeout=20, name='trades_api'),
    'book_ticker': CircuitBreaker(failure_threshold=8, timeout=30, recovery_timeout=15, name='book_ticker_api')
}