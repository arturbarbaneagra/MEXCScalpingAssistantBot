import asyncio
import time
from typing import Dict, Optional, Any
from logger import bot_logger
from circuit_breaker import api_circuit_breakers

class APIRecoveryManager:
    """Менеджер восстановления API с graceful degradation"""

    def __init__(self):
        self.fallback_data = {}
        self.last_successful_data = {}
        self.recovery_attempts = {}
        self.max_recovery_attempts = 3

    def store_successful_data(self, endpoint: str, symbol: str, data: Any):
        """Сохраняет успешные данные для fallback"""
        key = f"{endpoint}:{symbol}"
        self.last_successful_data[key] = {
            'data': data,
            'timestamp': time.time()
        }

    def get_fallback_data(self, endpoint: str, symbol: str) -> Optional[Any]:
        """Получает fallback данные при недоступности API"""
        key = f"{endpoint}:{symbol}"
        if key in self.last_successful_data:
            stored = self.last_successful_data[key]
            # Возвращаем данные если они не старше 5 минут
            if time.time() - stored['timestamp'] < 300:
                bot_logger.debug(f"Используем fallback данные для {key}")
                return stored['data']
        return None

    async def attempt_recovery(self, circuit_breaker_name: str) -> bool:
        """Попытка восстановления Circuit Breaker"""
        if circuit_breaker_name not in self.recovery_attempts:
            self.recovery_attempts[circuit_breaker_name] = 0

        if self.recovery_attempts[circuit_breaker_name] >= self.max_recovery_attempts:
            return False

        self.recovery_attempts[circuit_breaker_name] += 1

        # Сбрасываем Circuit Breaker для попытки восстановления
        if circuit_breaker_name in api_circuit_breakers:
            cb = api_circuit_breakers[circuit_breaker_name]
            cb.reset()
            bot_logger.info(f"🔄 Попытка восстановления Circuit Breaker '{circuit_breaker_name}' #{self.recovery_attempts[circuit_breaker_name]}")

            # Ждем перед следующей попыткой
            await asyncio.sleep(5.0)
            return True

        return False

    def reset_recovery_attempts(self, circuit_breaker_name: str):
        """Сбрасывает счетчик попыток восстановления при успешном восстановлении"""
        if circuit_breaker_name in self.recovery_attempts:
            del self.recovery_attempts[circuit_breaker_name]

    def get_api_health_status(self) -> Dict[str, Any]:
        """Возвращает статус здоровья API"""
        from circuit_breaker import api_circuit_breakers

        status = {
            'recovery_attempts': dict(self.recovery_attempts),
            'circuit_breakers': {},
            'fallback_data_count': len(self.last_successful_data)
        }

        # Статус Circuit Breaker'ов
        for name, cb in api_circuit_breakers.items():
            status['circuit_breakers'][name] = cb.get_status()

        return status

# Глобальный экземпляр
api_recovery_manager = APIRecoveryManager()