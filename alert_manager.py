
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

class AlertManager:
    def __init__(self):
        self.alerts: Dict[str, Alert] = {}
        self.legacy_alerts: Dict[str, Dict] = {}  # Для совместимости
        self.alert_history: List[Dict] = []
        self.max_history = 1000
        self.notification_callbacks: List[Callable] = []
        
        # Пороги для алертов
        self.thresholds = {
            'high_memory_usage': 85,  # %
            'high_cpu_usage': 80,     # %
            'slow_api_response': 5.0,  # секунд
            'api_error_rate': 10,     # %
            'low_disk_space': 90      # %
        }
        
        # Настройка продвинутых алертов
        self._setup_advanced_alerts()

    def check_system_alerts(self, system_info: Dict[str, Any]) -> List[Dict]:
        """Проверяет системные алерты"""
        alerts = []
        
        # Проверка памяти
        memory_percent = system_info.get('memory_percent', 0)
        if memory_percent > self.thresholds['high_memory_usage']:
            alerts.append({
                'type': 'high_memory_usage',
                'severity': 'warning',
                'message': f"Высокое использование памяти: {memory_percent:.1f}%",
                'value': memory_percent,
                'threshold': self.thresholds['high_memory_usage']
            })

        # Проверка CPU
        cpu_percent = system_info.get('cpu_percent', 0)
        if cpu_percent > self.thresholds['high_cpu_usage']:
            alerts.append({
                'type': 'high_cpu_usage',
                'severity': 'warning',
                'message': f"Высокое использование CPU: {cpu_percent:.1f}%",
                'value': cpu_percent,
                'threshold': self.thresholds['high_cpu_usage']
            })

        # Проверка диска
        disk_percent = system_info.get('disk_percent', 0)
        if disk_percent > self.thresholds['low_disk_space']:
            alerts.append({
                'type': 'low_disk_space',
                'severity': 'critical',
                'message': f"Мало места на диске: {disk_percent:.1f}%",
                'value': disk_percent,
                'threshold': self.thresholds['low_disk_space']
            })

        return alerts

    def check_api_alerts(self, api_stats: Dict[str, Any]) -> List[Dict]:
        """Проверяет алерты API"""
        alerts = []
        
        for endpoint, stats in api_stats.items():
            # Проверка времени ответа
            avg_time = stats.get('avg_response_time', 0)
            if avg_time > self.thresholds['slow_api_response']:
                alerts.append({
                    'type': 'slow_api_response',
                    'severity': 'warning',
                    'message': f"Медленный ответ API {endpoint}: {avg_time:.2f}с",
                    'endpoint': endpoint,
                    'value': avg_time,
                    'threshold': self.thresholds['slow_api_response']
                })

            # Проверка уровня ошибок
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
                        'threshold': self.thresholds['api_error_rate']
                    })

        return alerts

    def process_alerts(self, alerts: List[Dict]) -> None:
        """Обрабатывает алерты"""
        current_time = time.time()
        
        for alert in alerts:
            alert_id = f"{alert['type']}_{alert.get('endpoint', 'system')}"
            
            # Проверяем, не дублируется ли алерт
            if alert_id in self.alerts:
                last_alert_time = self.alerts[alert_id].get('last_seen', 0)
                # Не отправляем дубликаты чаще раза в 5 минут
                if current_time - last_alert_time < 300:
                    continue
            
            # Записываем алерт
            alert['timestamp'] = current_time
            alert['alert_id'] = alert_id
            self.alerts[alert_id] = alert
            
            # Добавляем в историю
            self.alert_history.append(alert.copy())
            if len(self.alert_history) > self.max_history:
                self.alert_history.pop(0)
            
            # Логируем алерт
            severity_emoji = {
                'info': 'ℹ️',
                'warning': '⚠️',
                'critical': '🚨'
            }
            emoji = severity_emoji.get(alert['severity'], '❗')
            bot_logger.warning(f"{emoji} ALERT: {alert['message']}")

    def get_active_alerts(self) -> List[Dict]:
        """Возвращает активные алерты"""
        current_time = time.time()
        active_alerts = []
        
        for alert_id, alert in self.alerts.items():
            # Алерт считается активным если он был в последние 10 минут
            if current_time - alert.get('timestamp', 0) < 600:
                active_alerts.append(alert)
        
        return active_alerts

    def get_alert_summary(self) -> Dict[str, Any]:
        """Возвращает сводку алертов"""
        active_alerts = self.get_active_alerts()
        
        return {
            'active_count': len(active_alerts),
            'active_alerts': active_alerts,
            'recent_history': self.alert_history[-10:],  # Последние 10
            'total_alerts_today': len([a for a in self.alert_history 
                                     if time.time() - a.get('timestamp', 0) < 86400])
        }

# Глобальный экземпляр менеджера алертов
alert_manager = AlertManager()
