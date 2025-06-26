
import time
import asyncio
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
from dataclasses import dataclass
from logger import bot_logger

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
    """Унифицированный класс алерта"""
    
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
        self.condition_start_times: Dict[str, float] = {}

    def check_conditions(self, data: Dict) -> bool:
        """Проверяет условия срабатывания алерта"""
        if not self.is_active:
            return False
            
        current_time = time.time()
        
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
        
        condition_met = self._evaluate_condition(value, condition.operator, condition.value)
        
        if condition_met:
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
            return abs(value - target) < 0.0001
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

class UnifiedAlertManager:
    """Единая система управления всеми алертами"""
    
    def __init__(self):
        self.alerts: Dict[str, Alert] = {}
        self.legacy_alerts: Dict[str, Dict] = {}
        self.alert_history: List[Dict] = []
        self.max_history = 1000
        self.notification_callbacks: List[Callable] = []
        
        # Пороги для системных алертов
        self.thresholds = {
            'high_memory_usage': 85,
            'high_cpu_usage': 80,
            'slow_api_response': 3.0,
            'api_error_rate': 10,
            'low_disk_space': 90
        }
        
        self._setup_all_alerts()

    def _setup_all_alerts(self):
        """Настройка всех типов алертов"""
        
        # Алерт высокого объема
        volume_spike_alert = Alert(
            alert_id="volume_spike",
            alert_type=AlertType.VOLUME_SPIKE,
            severity=AlertSeverity.WARNING,
            title="🚀 Всплеск объема",
            message="Обнаружен необычно высокий объем торгов",
            conditions=[
                AlertCondition("volume", ">", 10000, duration=30)
            ],
            cooldown=180
        )
        
        # Алерт резкого движения цены
        price_movement_alert = Alert(
            alert_id="price_movement",
            alert_type=AlertType.PRICE_MOVEMENT,
            severity=AlertSeverity.CRITICAL,
            title="⚡ Резкое движение цены",
            message="Обнаружено резкое изменение цены",
            conditions=[
                AlertCondition("change", ">", 15, duration=0),
                AlertCondition("volume", ">", 5000, duration=0)
            ],
            cooldown=120
        )
        
        # Алерт аномального спреда
        spread_alert = Alert(
            alert_id="spread_anomaly",
            alert_type=AlertType.SPREAD_ANOMALY,
            severity=AlertSeverity.WARNING,
            title="📊 Аномальный спред",
            message="Обнаружен необычно высокий спред",
            conditions=[
                AlertCondition("spread", ">", 2.0, duration=60)
            ],
            cooldown=300
        )
        
        # Системные алерты
        performance_alert = Alert(
            alert_id="system_performance",
            alert_type=AlertType.SYSTEM_PERFORMANCE,
            severity=AlertSeverity.WARNING,
            title="🔧 Проблемы производительности",
            message="Обнаружены проблемы с производительностью системы",
            conditions=[
                AlertCondition("avg_response_time", ">", 2.0, duration=120)
            ],
            cooldown=900
        )
        
        critical_performance_alert = Alert(
            alert_id="critical_performance",
            alert_type=AlertType.SYSTEM_PERFORMANCE,
            severity=AlertSeverity.CRITICAL,
            title="🚨 Критические проблемы",
            message="Система работает на пределе возможностей",
            conditions=[
                AlertCondition("memory_percent", ">", 95, duration=60)
            ],
            cooldown=300
        )
        
        self.add_alert(volume_spike_alert)
        self.add_alert(price_movement_alert)
        self.add_alert(spread_alert)
        self.add_alert(performance_alert)
        self.add_alert(critical_performance_alert)

    def add_alert(self, alert: Alert):
        """Добавляет алерт"""
        self.alerts[alert.alert_id] = alert
        bot_logger.info(f"Добавлен алерт: {alert.alert_id} ({alert.severity.value})")

    def check_coin_alerts(self, symbol: str, coin_data: Dict):
        """Проверяет алерты для конкретной монеты"""
        for alert in self.alerts.values():
            if alert.alert_type in [AlertType.VOLUME_SPIKE, AlertType.PRICE_MOVEMENT, AlertType.SPREAD_ANOMALY]:
                if alert.check_conditions(coin_data):
                    self._trigger_alert(alert, symbol, coin_data)

    def check_system_alerts(self, system_info: Dict[str, Any]) -> List[Dict]:
        """Проверяет системные алерты (старый + новый API)"""
        alerts = []
        
        # Старый API для совместимости
        memory_percent = system_info.get('memory_percent', 0)
        if memory_percent > self.thresholds['high_memory_usage']:
            alerts.append({
                'type': 'high_memory_usage',
                'severity': 'warning',
                'message': f"Высокое использование памяти: {memory_percent:.1f}%",
                'value': memory_percent,
                'threshold': self.thresholds['high_memory_usage'],
                'timestamp': time.time()
            })

        cpu_percent = system_info.get('cpu_percent', 0)
        if cpu_percent > self.thresholds['high_cpu_usage']:
            alerts.append({
                'type': 'high_cpu_usage',
                'severity': 'warning',
                'message': f"Высокое использование CPU: {cpu_percent:.1f}%",
                'value': cpu_percent,
                'threshold': self.thresholds['high_cpu_usage'],
                'timestamp': time.time()
            })

        # Новый API через Alert объекты
        for alert in self.alerts.values():
            if alert.alert_type == AlertType.SYSTEM_PERFORMANCE:
                if alert.check_conditions(system_info):
                    self._trigger_alert(alert, "SYSTEM", system_info)

        return alerts

    def check_api_alerts(self, api_stats: Dict[str, Any]) -> List[Dict]:
        """Проверяет алерты API"""
        alerts = []
        
        for endpoint, stats in api_stats.items():
            avg_time = stats.get('avg_response_time', 0)
            if avg_time > self.thresholds['slow_api_response']:
                alerts.append({
                    'type': 'slow_api_response',
                    'severity': 'warning',
                    'message': f"Медленный ответ API {endpoint}: {avg_time:.2f}с",
                    'endpoint': endpoint,
                    'value': avg_time,
                    'threshold': self.thresholds['slow_api_response'],
                    'timestamp': time.time()
                })

            total_requests = stats.get('total_requests', 0)
            error_count = stats.get('error_count', 0)
            if total_requests > 0:
                error_rate = (error_count / total_requests) * 100
                if error_rate > self.thresholds['api_error_rate']:
                    alerts.append({
                        'type': 'high_api_error_rate',
                        'severity': 'critical',
                        'message': f"Высокий уровень ошибок API {endpoint}: {error_rate:.1f}%",
                        'endpoint': endpoint,
                        'value': error_rate,
                        'threshold': self.thresholds['api_error_rate'],
                        'timestamp': time.time()
                    })

        return alerts

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
        
        self.alert_history.append(alert_data)
        if len(self.alert_history) > self.max_history:
            self.alert_history = self.alert_history[-500:]
        
        for callback in self.notification_callbacks:
            try:
                callback(alert_data)
            except Exception as e:
                bot_logger.error(f"Ошибка в notification callback: {e}")
        
        bot_logger.warning(
            f"🚨 АЛЕРТ [{alert.severity.value.upper()}] {alert.title} "
            f"для {symbol}: {alert.message}"
        )

    def process_alerts(self, alerts: List[Dict]) -> None:
        """Обрабатывает алерты (для совместимости)"""
        current_time = time.time()
        
        for alert in alerts:
            alert_id = f"{alert['type']}_{alert.get('endpoint', 'system')}"
            
            if alert_id in self.legacy_alerts:
                last_alert_time = self.legacy_alerts[alert_id].get('last_seen', 0)
                if current_time - last_alert_time < 300:
                    continue
            
            alert['timestamp'] = current_time
            alert['alert_id'] = alert_id
            self.legacy_alerts[alert_id] = alert
            
            self.alert_history.append(alert.copy())
            if len(self.alert_history) > self.max_history:
                self.alert_history.pop(0)
            
            severity_emoji = {
                'info': 'ℹ️',
                'warning': '⚠️',
                'critical': '🚨'
            }
            emoji = severity_emoji.get(alert['severity'], '❗')
            bot_logger.warning(f"{emoji} ALERT: {alert['message']}")

    def get_active_alerts(self) -> List[Dict]:
        """Возвращает активные алерты (все типы)"""
        current_time = time.time()
        active_alerts = []
        
        # Новые алерты через Alert объекты
        for alert in self.alerts.values():
            if alert.is_active and alert.last_triggered > 0:
                if current_time - alert.last_triggered < alert.cooldown * 2:
                    active_alerts.append({
                        'alert_id': alert.alert_id,
                        'type': alert.alert_type.value,
                        'severity': alert.severity.value,
                        'title': alert.title,
                        'last_triggered': alert.last_triggered,
                        'trigger_count': alert.trigger_count
                    })
        
        # Старые алерты для совместимости
        for alert_id, alert in self.legacy_alerts.items():
            if current_time - alert.get('timestamp', 0) < 600:
                active_alerts.append(alert)
        
        return active_alerts

    def get_alert_history(self, limit: int = 50) -> List[Dict]:
        """Возвращает историю алертов"""
        return self.alert_history[-limit:]

    def get_alert_stats(self) -> Dict:
        """Возвращает статистику алертов"""
        stats = {
            'total_alerts': len(self.alerts),
            'active_alerts': len([a for a in self.alerts.values() if a.is_active]),
            'total_triggers': sum(a.trigger_count for a in self.alerts.values()),
            'history_size': len(self.alert_history)
        }
        
        by_type = {}
        for alert in self.alerts.values():
            alert_type = alert.alert_type.value
            if alert_type not in by_type:
                by_type[alert_type] = {'count': 0, 'triggers': 0}
            by_type[alert_type]['count'] += 1
            by_type[alert_type]['triggers'] += alert.trigger_count
        
        stats['by_type'] = by_type
        return stats

    def get_alert_summary(self) -> Dict[str, Any]:
        """Возвращает сводку алертов (для совместимости)"""
        active_alerts = self.get_active_alerts()
        
        return {
            'active_count': len(active_alerts),
            'active_alerts': active_alerts,
            'recent_history': self.alert_history[-10:],
            'total_alerts_today': len([a for a in self.alert_history 
                                     if time.time() - a.get('timestamp', 0) < 86400])
        }

    def add_notification_callback(self, callback: Callable):
        """Добавляет callback для уведомлений"""
        self.notification_callbacks.append(callback)

# Глобальный экземпляр единой системы алертов
alert_manager = UnifiedAlertManager()

# Алиасы для совместимости
advanced_alert_manager = alert_manager
