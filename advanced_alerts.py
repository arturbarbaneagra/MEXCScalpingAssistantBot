from alert_manager import alert_manager as advanced_alert_manager

# Экспортируем необходимые классы для совместимости
from alert_manager import AlertSeverity, AlertType, AlertCondition, Alert

# Обратная совместимость
def get_active_alerts():
    return advanced_alert_manager.get_active_alerts()

def get_alert_stats():
    return advanced_alert_manager.get_alert_stats()

def check_coin_alerts(symbol, coin_data):
    return advanced_alert_manager.check_coin_alerts(symbol, coin_data)