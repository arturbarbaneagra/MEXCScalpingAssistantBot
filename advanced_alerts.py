
import time
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
from dataclasses import dataclass
from logger import bot_logger

class AlertType(Enum):
    """Типы алертов"""
    VOLUME_SPIKE = "volume_spike"
    PRICE_MOVEMENT = "price_movement"
    SPREAD_ANOMALY = "spread_anomaly"
    API_ERROR = "api_error"
    SYSTEM_PERFORMANCE = "system_performance"
    UNUSUAL_ACTIVITY = "unusual_activity"

class AlertSeverity(Enum):
    """Уровни важности алертов"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"

@dataclass
class AlertCondition:
    """Условие для срабатывания алерта"""
    field: str
    operator: str  # >, <, >=, <=, ==, !=
    value: float
    duration: int = 0  # Время в секундах для подтверждения условия

class AdvancedAlert:
    """Продвинутый алерт"""
    
    def __init__(
        self,
        alert_id: str,
        alert_type: AlertType,
        severity: AlertSeverity,
        title: str,
        message: str,
        conditions: List[AlertCondition] = None,
        callback: Optional[Callable] = None,
        cooldown: int = 300
    ):
        self.alert_id = alert_id
        self.alert_type = alert_type
        self.severity = severity
        self.title = title
        self.message = message
        self.conditions = conditions or []
        self.callback = callback
        self.cooldown = cooldown
        
        self.created_at = time.time()
        self.last_triggered = 0
        self.trigger_count = 0
        self.is_active = True

    def check_conditions(self, data: Dict[str, Any]) -> bool:
        """Проверяет условия срабатывания алерта"""
        if not self.conditions:
            return False
            
        for condition in self.conditions:
            if not self._check_single_condition(condition, data):
                return False
        return True

    def _check_single_condition(self, condition: AlertCondition, data: Dict[str, Any]) -> bool:
        """Проверяет одно условие"""
        value = data.get(condition.field)
        if value is None:
            return False
            
        if condition.operator == '>':
            return value > condition.value
        elif condition.operator == '<':
            return value < condition.value
        elif condition.operator == '>=':
            return value >= condition.value
        elif condition.operator == '<=':
            return value <= condition.value
        elif condition.operator == '==':
            return value == condition.value
        elif condition.operator == '!=':
            return value != condition.value
        return False

    def trigger(self):
        """Срабатывание алерта"""
        current_time = time.time()
        
        # Проверяем cooldown
        if current_time - self.last_triggered < self.cooldown:
            return
            
        self.last_triggered = current_time
        self.trigger_count += 1
        
        bot_logger.warning(f"🚨 ALERT [{self.severity.value.upper()}]: {self.title}")
        
        if self.callback:
            try:
                self.callback(self)
            except Exception as e:
                bot_logger.error(f"Ошибка в callback алерта {self.alert_id}: {e}")

class AdvancedAlertManager:
    """Менеджер продвинутых алертов"""
    
    def __init__(self):
        self.alerts: Dict[str, AdvancedAlert] = {}
        self.alert_history: List[Dict] = []
        self.max_history = 500
        
        # Настраиваем стандартные алерты
        self._setup_default_alerts()

    def _setup_default_alerts(self):
        """Настраивает стандартные алерты"""
        # Алерт на резкий рост объема
        self.add_alert(
            alert_id="volume_spike",
            alert_type=AlertType.VOLUME_SPIKE,
            severity=AlertSeverity.WARNING,
            title="Резкий рост объема",
            message="Обнаружен резкий рост объема торгов",
            conditions=[
                AlertCondition("volume", ">", 10000),
                AlertCondition("volume_change_percent", ">", 200)
            ]
        )

        # Алерт на большое движение цены
        self.add_alert(
            alert_id="price_movement",
            alert_type=AlertType.PRICE_MOVEMENT,
            severity=AlertSeverity.INFO,
            title="Значительное движение цены",
            message="Зафиксировано значительное движение цены",
            conditions=[
                AlertCondition("change", ">", 5.0)
            ]
        )

    def add_alert(
        self,
        alert_id: str,
        alert_type: AlertType,
        severity: AlertSeverity,
        title: str,
        message: str,
        conditions: List[AlertCondition] = None,
        callback: Optional[Callable] = None,
        cooldown: int = 300
    ):
        """Добавляет новый алерт"""
        alert = AdvancedAlert(
            alert_id=alert_id,
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            conditions=conditions,
            callback=callback,
            cooldown=cooldown
        )
        self.alerts[alert_id] = alert
        bot_logger.debug(f"Добавлен алерт: {alert_id}")

    def remove_alert(self, alert_id: str):
        """Удаляет алерт"""
        if alert_id in self.alerts:
            del self.alerts[alert_id]
            bot_logger.debug(f"Удален алерт: {alert_id}")

    def check_coin_alerts(self, symbol: str, coin_data: Dict[str, Any]):
        """Проверяет алерты для конкретной монеты"""
        for alert in self.alerts.values():
            if not alert.is_active:
                continue
                
            if alert.check_conditions(coin_data):
                alert.trigger()
                
                # Добавляем в историю
                self.alert_history.append({
                    'timestamp': time.time(),
                    'alert_id': alert.alert_id,
                    'symbol': symbol,
                    'title': alert.title,
                    'severity': alert.severity.value,
                    'message': alert.message,
                    'data': coin_data.copy()
                })
                
                # Ограничиваем размер истории
                if len(self.alert_history) > self.max_history:
                    self.alert_history.pop(0)

    def get_active_alerts(self) -> List[Dict]:
        """Возвращает активные алерты"""
        return [
            {
                'alert_id': alert.alert_id,
                'title': alert.title,
                'severity': alert.severity.value,
                'type': alert.alert_type.value,
                'trigger_count': alert.trigger_count,
                'last_triggered': alert.last_triggered
            }
            for alert in self.alerts.values()
            if alert.is_active
        ]

    def get_alert_history(self, limit: int = 10) -> List[Dict]:
        """Возвращает историю алертов"""
        return sorted(
            self.alert_history[-limit:],
            key=lambda x: x['timestamp'],
            reverse=True
        )

    def get_alert_stats(self) -> Dict[str, Any]:
        """Возвращает статистику алертов"""
        active_count = sum(1 for alert in self.alerts.values() if alert.is_active)
        total_triggers = sum(alert.trigger_count for alert in self.alerts.values())
        
        return {
            'total_alerts': len(self.alerts),
            'active_alerts': active_count,
            'total_triggers': total_triggers,
            'history_size': len(self.alert_history)
        }

    def toggle_alert(self, alert_id: str, active: bool = None):
        """Включает/выключает алерт"""
        if alert_id in self.alerts:
            if active is None:
                self.alerts[alert_id].is_active = not self.alerts[alert_id].is_active
            else:
                self.alerts[alert_id].is_active = active
            bot_logger.debug(f"Алерт {alert_id} {'включен' if self.alerts[alert_id].is_active else 'выключен'}")

    def clear_history(self):
        """Очищает историю алертов"""
        self.alert_history.clear()
        bot_logger.info("История алертов очищена")

# Глобальный экземпляр менеджера
advanced_alert_manager = AdvancedAlertManager()
