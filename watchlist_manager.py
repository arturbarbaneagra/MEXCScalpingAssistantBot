
import json
import os
from typing import Set
from datetime import datetime
from logger import bot_logger

class WatchlistManager:
    def __init__(self, watchlist_file: str = "watchlist.json"):
        self.watchlist_file = watchlist_file
        self.watchlist: Set[str] = set()
        self.load_watchlist()
    
    def load_watchlist(self):
        """Загружает список монет из файла"""
        try:
            if os.path.exists(self.watchlist_file):
                with open(self.watchlist_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.watchlist = set(data)
                    elif isinstance(data, dict) and 'coins' in data:
                        self.watchlist = set(data['coins'])
                    else:
                        self.watchlist = set()
                    bot_logger.info(f"Загружен список из {len(self.watchlist)} монет")
            else:
                # Создаем файл с начальным списком
                default_coins = [
                    "BTC", "ETH", "BNB", "ADA", "DOT", "LINK", "UNI", "LTC",
                    "XRP", "BCH", "VET", "FIL", "TRX", "EOS", "XLM", "ATOM"
                ]
                self.watchlist = set(default_coins)
                self.save_watchlist()
                bot_logger.info(f"Создан новый список с {len(self.watchlist)} монетами")
        except Exception as e:
            bot_logger.error(f"Ошибка загрузки списка: {e}")
            self.watchlist = set()
    
    def save_watchlist(self):
        """Сохраняет список монет в файл"""
        try:
            data = {
                'coins': sorted(list(self.watchlist)),
                'last_updated': str(datetime.now())
            }
            with open(self.watchlist_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            bot_logger.error(f"Ошибка сохранения списка: {e}")
    
    def add(self, symbol: str) -> bool:
        """Добавляет монету в список"""
        symbol = symbol.upper().replace("_USDT", "").replace("USDT", "")
        if symbol not in self.watchlist:
            self.watchlist.add(symbol)
            self.save_watchlist()
            bot_logger.info(f"Добавлена монета: {symbol}")
            return True
        return False
    
    def remove(self, symbol: str) -> bool:
        """Удаляет монету из списка"""
        symbol = symbol.upper().replace("_USDT", "").replace("USDT", "")
        if symbol in self.watchlist:
            self.watchlist.remove(symbol)
            self.save_watchlist()
            bot_logger.info(f"Удалена монета: {symbol}")
            return True
        return False
    
    def contains(self, symbol: str) -> bool:
        """Проверяет наличие монеты в списке"""
        symbol = symbol.upper().replace("_USDT", "").replace("USDT", "")
        return symbol in self.watchlist
    
    def get_all(self) -> Set[str]:
        """Возвращает все монеты"""
        return self.watchlist.copy()
    
    def size(self) -> int:
        """Возвращает количество монет в списке"""
        return len(self.watchlist)
    
    def clear(self):
        """Очищает список"""
        self.watchlist.clear()
        self.save_watchlist()
        bot_logger.info("Список монет очищен")

# Глобальный экземпляр менеджера
watchlist_manager = WatchlistManager()
