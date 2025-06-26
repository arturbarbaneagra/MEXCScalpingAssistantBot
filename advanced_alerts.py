
import asyncio
import time
from typing import Dict, List, Optional, Callable
from enum import Enum
from dataclasses import dataclass, field
from logger import bot_logger
from config import config_manager

class AlertSeverity(Enum):
    """Уровни важности алертов"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"

class AlertType(Enum):
    """Типы алертов"""
    VOLUME_SPIKE = "volume_spike"
    PRICE_MOVEMENT = "price_movement"
    SPREAD_ANOMALY = "spread_anomaly"
    API_ERROR = "api_error"
    SYSTEM_PERFORMANCE = "system_performance"
    UNUSUAL_ACTIVITY = "unusual_activity"

@dataclass
class AlertCondition:
    """Условие для срабатывания алерта"""
    field: str
    operator: str  # >, <, >=, <=, ==, !=
    value: float
    duration: int = 0  # Время в секундах для подтверждения условия
    
class Alert:
    """Класс алерта"""
    
    def __init__(
        self,
        alert_id: str,
        alert_type: AlertType,
        severity: AlertSeverity,
        title: str,
        message: str,
        conditions: List[AlertCondition] = None,
        callback: Optional[Callable] = None,
        cooldown: int = 300  # 5 минут между повторными алертами
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
        self.condition_start_times: Dict[str, float] = {}

    def check_conditions(self, data: Dict) -> bool:
        """Проверяет условия срабатывания алерта"""
        if not self.is_active:
            return False
            
        current_time = time.time()
        
        # Проверка cooldown
        if current_time - self.last_triggered < self.cooldown:
            return False
            
        for condition in self.conditions:
            if not self._check_single_condition(condition, data, current_time):
                return False
                
        return True
    
    def _check_single_condition(self, condition: AlertCondition, data: Dict, current_time: float) -> bool:
        """Проверяет одно условие"""
        if condition.field not in data:
            return False
            
        value = data[condition.field]
        condition_key = f"{condition.field}_{condition.operator}_{condition.value}"
        
        # Проверяем математическое условие
        condition_met = self._evaluate_condition(value, condition.operator, condition.value)
        
        if condition_met:
            # Если условие выполнено, проверяем длительность
            if condition.duration > 0:
                if condition_key not in self.condition_start_times:
                    self.condition_start_times[condition_key] = current_time
                    return False
                elif current_time - self.condition_start_times[condition_key] >= condition.duration:
                    return True
                else:
                    return False
            else:
                return True
        else:
            # Условие не выполнено, сбрасываем таймер
            if condition_key in self.condition_start_times:
                del self.condition_start_times[condition_key]
            return False
    
    def _evaluate_condition(self, value: float, operator: str, target: float) -> bool:
        """Оценивает математическое условие"""
        if operator == '>':
            return value > target
        elif operator == '<':
            return value < target
        elif operator == '>=':
            return value >= target
        elif operator == '<=':
            return value <= target
        elif operator == '==':
            return abs(value - target) < 0.0001  # Для float сравнения
        elif operator == '!=':
            return abs(value - target) >= 0.0001
        return False
    
    def trigger(self):
        """Срабатывание алерта"""
        self.last_triggered = time.time()
        self.trigger_count += 1
        
        if self.callback:
            try:
                self.callback(self)
            except Exception as e:
                bot_logger.error(f"Ошибка в callback алерта {self.alert_id}: {e}")

class AdvancedAlertManager:
    """Продвинутый менеджер алертов"""
    
    def __init__(self):
        self.alerts: Dict[str, Alert] = {}
        self.alert_history: List[Dict] = []
        self.notification_callbacks: List[Callable] = []
        
        # Настройка стандартных алертов
        self._setup_default_alerts()
        
    def _setup_default_alerts(self):
        """Настройка стандартных алертов"""
        
        # Алерт высокого объема
        volume_spike_alert = Alert(
            alert_id="volume_spike",
            alert_type=AlertType.VOLUME_SPIKE,
            severity=AlertSeverity.WARNING,
            title="🚀 Всплеск объема",
            message="Обнаружен необычно высокий объем торгов",
            conditions=[
                AlertCondition("volume", ">", 10000, duration=30)  # 30 сек подтверждения
            ],
            cooldown=180  # 3 минуты
        )
        
        # Алерт резкого движения цены
        price_movement_alert = Alert(
            alert_id="price_movement",
            alert_type=AlertType.PRICE_MOVEMENT,
            severity=AlertSeverity.CRITICAL,
            title="⚡ Резкое движение цены",
            message="Обнаружено резкое изменение цены",
            conditions=[
                AlertCondition("change", ">", 15, duration=0),  # Мгновенно
                AlertCondition("volume", ">", 5000, duration=0)  # С подтверждением объемом
            ],
            cooldown=120  # 2 минуты
        )
        
        # Алерт аномального спреда
        spread_alert = Alert(
            alert_id="spread_anomaly",
            alert_type=AlertType.SPREAD_ANOMALY,
            severity=AlertSeverity.WARNING,
            title="📊 Аномальный спред",
            message="Обнаружен необычно высокий спред",
            conditions=[
                AlertCondition("spread", ">", 2.0, duration=60)  # 1 минута подтверждения
            ],
            cooldown=300  # 5 минут
        )
        
        # Алерт производительности системы
        performance_alert = Alert(
            alert_id="system_performance",
            alert_type=AlertType.SYSTEM_PERFORMANCE,
            severity=AlertSeverity.CRITICAL,
            title="🔧 Проблемы производительности",
            message="Обнаружены проблемы с производительностью системы",
            cooldown=600  # 10 минут
        )
        
        self.add_alert(volume_spike_alert)
        self.add_alert(price_movement_alert)
        self.add_alert(spread_alert)
        self.add_alert(performance_alert)
        
    def add_alert(self, alert: Alert):
        """Добавляет алерт"""
        self.alerts[alert.alert_id] = alert
        bot_logger.info(f"Добавлен алерт: {alert.alert_id} ({alert.severity.value})")
    
    def remove_alert(self, alert_id: str) -> bool:
        """Удаляет алерт"""
        if alert_id in self.alerts:
            del self.alerts[alert_id]
            bot_logger.info(f"Удален алерт: {alert_id}")
            return True
        return False
    
    def check_coin_alerts(self, symbol: str, coin_data: Dict):
        """Проверяет алерты для конкретной монеты"""
        for alert in self.alerts.values():
            if alert.check_conditions(coin_data):
                self._trigger_alert(alert, symbol, coin_data)
    
    def check_system_alerts(self, system_data: Dict):
        """Проверяет системные алерты"""
        system_alerts = [
            alert for alert in self.alerts.values() 
            if alert.alert_type == AlertType.SYSTEM_PERFORMANCE
        ]
        
        for alert in system_alerts:
            if alert.check_conditions(system_data):
                self._trigger_alert(alert, "SYSTEM", system_data)
    
    def _trigger_alert(self, alert: Alert, symbol: str, data: Dict):
        """Запускает алерт"""
        alert.trigger()
        
        alert_data = {
            'alert_id': alert.alert_id,
            'symbol': symbol,
            'type': alert.alert_type.value,
            'severity': alert.severity.value,
            'title': alert.title,
            'message': alert.message,
            'data': data,
            'timestamp': time.time(),
            'trigger_count': alert.trigger_count
        }
        
        # Добавляем в историю
        self.alert_history.append(alert_data)
        
        # Ограничиваем размер истории
        if len(self.alert_history) > 1000:
            self.alert_history = self.alert_history[-500:]
        
        # Уведомляем подписчиков
        for callback in self.notification_callbacks:
            try:
                callback(alert_data)
            except Exception as e:
                bot_logger.error(f"Ошибка в notification callback: {e}")
        
        bot_logger.warning(
            f"🚨 АЛЕРТ [{alert.severity.value.upper()}] {alert.title} "
            f"для {symbol}: {alert.message}"
        )
    
    def add_notification_callback(self, callback: Callable):
        """Добавляет callback для уведомлений"""
        self.notification_callbacks.append(callback)
    
    def get_active_alerts(self) -> List[Dict]:
        """Возвращает активные алерты"""
        current_time = time.time()
        active_alerts = []
        
        for alert in self.alerts.values():
            if alert.is_active and alert.last_triggered > 0:
                # Считаем алерт активным если он сработал недавно
                if current_time - alert.last_triggered < alert.cooldown * 2:
                    active_alerts.append({
                        'alert_id': alert.alert_id,
                        'type': alert.alert_type.value,
                        'severity': alert.severity.value,
                        'title': alert.title,
                        'last_triggered': alert.last_triggered,
                        'trigger_count': alert.trigger_count
                    })
        
        return active_alerts
    
    def get_alert_history(self, limit: int = 50) -> List[Dict]:
        """Возвращает историю алертов"""
        return self.alert_history[-limit:]
    
    def clear_history(self):
        """Очищает историю алертов"""
        self.alert_history.clear()
        bot_logger.info("История алертов очищена")
    
    def set_alert_active(self, alert_id: str, active: bool):
        """Включает/выключает алерт"""
        if alert_id in self.alerts:
            self.alerts[alert_id].is_active = active
            status = "включен" if active else "выключен"
            bot_logger.info(f"Алерт {alert_id} {status}")
            return True
        return False
    
    def get_alert_stats(self) -> Dict:
        """Возвращает статистику алертов"""
        stats = {
            'total_alerts': len(self.alerts),
            'active_alerts': len([a for a in self.alerts.values() if a.is_active]),
            'total_triggers': sum(a.trigger_count for a in self.alerts.values()),
            'history_size': len(self.alert_history)
        }
        
        # Статистика по типам
        by_type = {}
        for alert in self.alerts.values():
            alert_type = alert.alert_type.value
            if alert_type not in by_type:
                by_type[alert_type] = {'count': 0, 'triggers': 0}
            by_type[alert_type]['count'] += 1
            by_type[alert_type]['triggers'] += alert.trigger_count
        
        stats['by_type'] = by_type
        return stats

# Глобальный экземпляр продвинутого менеджера алертов
advanced_alert_manager = AdvancedAlertManager()
