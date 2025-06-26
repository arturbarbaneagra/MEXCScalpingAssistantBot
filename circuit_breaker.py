
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
        failure_threshold: int = 8,  # Увеличили порог
        timeout: float = 60.0,
        recovery_timeout: float = 20.0,  # Уменьшили время восстановления
        name: str = "circuit_breaker"
    ):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.recovery_timeout = recovery_timeout
        self.name = name
        
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = CircuitState.CLOSED
        self.success_count = 0  # Счетчик успешных операций
        
        bot_logger.debug(f"Инициализирован Circuit Breaker '{name}' (порог: {failure_threshold})")

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Выполняет функцию через Circuit Breaker с улучшенной логикой"""
        
        # Проверяем состояние
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
                bot_logger.debug(f"Circuit Breaker '{self.name}' переключен в HALF_OPEN")
            else:
                raise Exception(f"Circuit Breaker '{self.name}' OPEN - вызов заблокирован")
        
        try:
            # Выполняем функцию
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            # Успешное выполнение
            self.success_count += 1
            
            if self.state == CircuitState.HALF_OPEN:
                # Требуем несколько успешных вызовов для полного восстановления
                if self.success_count >= 3:
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.success_count = 0
                    bot_logger.info(f"Circuit Breaker '{self.name}' восстановлен - CLOSED")
            elif self.state == CircuitState.CLOSED:
                # Постепенно уменьшаем счетчик ошибок при успешных операциях
                if self.failure_count > 0 and self.success_count % 5 == 0:
                    self.failure_count = max(0, self.failure_count - 1)
            
            return result
            
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            self.success_count = 0
            
            # Проверяем превышение порога ошибок
            if self.failure_count >= self.failure_threshold:
                if self.state != CircuitState.OPEN:
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
            'success_count': self.success_count,
            'failure_threshold': self.failure_threshold,
            'last_failure_time': self.last_failure_time,
            'health_ratio': max(0, 1 - (self.failure_count / self.failure_threshold))
        }

    def reset(self):
        """Сбрасывает Circuit Breaker"""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0
        bot_logger.info(f"Circuit Breaker '{self.name}' сброшен")

    def force_open(self):
        """Принудительно открывает Circuit Breaker"""
        self.state = CircuitState.OPEN
        self.last_failure_time = time.time()
        bot_logger.warning(f"Circuit Breaker '{self.name}' принудительно открыт")

    def force_close(self):
        """Принудительно закрывает Circuit Breaker"""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        bot_logger.info(f"Circuit Breaker '{self.name}' принудительно закрыт")

# Оптимизированные Circuit Breaker для разных API endpoint'ов
api_circuit_breakers = {
    'ticker': CircuitBreaker(failure_threshold=10, recovery_timeout=15, name='ticker_api'),
    'klines': CircuitBreaker(failure_threshold=8, recovery_timeout=20, name='klines_api'),
    'trades': CircuitBreaker(failure_threshold=12, recovery_timeout=30, name='trades_api'),
    'book_ticker': CircuitBreaker(failure_threshold=10, recovery_timeout=15, name='book_ticker_api')
}
