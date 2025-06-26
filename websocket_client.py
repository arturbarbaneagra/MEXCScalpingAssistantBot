import asyncio
import json
import time
import websockets
from typing import Dict, List, Optional, Callable
from logger import bot_logger
from config import config_manager
from cache_manager import cache_manager

class WebSocketClient:
    def __init__(self):
        self.ws_url = "wss://ws.mexc.com/ws"
        self.websocket = None
        self.subscriptions = set()
        self.callbacks = {}
        self.running = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.last_ping = 0

    async def connect(self):
        """Подключение к WebSocket"""
        try:
            self.websocket = await websockets.connect(
                self.ws_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=10
            )
            self.running = True
            self.reconnect_attempts = 0
            bot_logger.info("🔌 WebSocket подключен")

            # Запускаем обработчики
            await asyncio.gather(
                self._message_handler(),
                self._ping_handler(),
                return_exceptions=True
            )

        except Exception as e:
            bot_logger.error(f"Ошибка WebSocket подключения: {e}")
            await self._handle_reconnect()

    async def _message_handler(self):
        """Обработчик входящих сообщений"""
        try:
            async for message in self.websocket:
                try:
                    data = json.loads(message)
                    await self._process_message(data)
                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    bot_logger.debug(f"Ошибка обработки сообщения: {e}")

        except websockets.exceptions.ConnectionClosed:
            bot_logger.warning("WebSocket соединение закрыто")
            if self.running:
                await self._handle_reconnect()
        except Exception as e:
            bot_logger.error(f"Ошибка в message_handler: {e}")
            if self.running:
                await self._handle_reconnect()

    async def _process_message(self, data: Dict):
        """Обрабатывает входящие данные"""
        if 'channel' in data and 'data' in data:
            channel = data['channel']

            # Обработка тикеров
            if channel.startswith('spot@public.miniTicker.v3.api@'):
                symbol = channel.split('@')[-1].replace('USDT', '')
                ticker_data = data['data']

                # Кешируем данные
                cache_manager.set_ticker_cache(symbol, {
                    'symbol': ticker_data['s'],
                    'lastPrice': ticker_data['c'],
                    'priceChangePercent': ticker_data['P'],
                    'volume': ticker_data['v'],
                    'quoteVolume': ticker_data['qv']
                })

                # Вызываем callback если есть
                if symbol in self.callbacks:
                    await self.callbacks[symbol](symbol, ticker_data)

            # Обработка book ticker
            elif channel.startswith('spot@public.bookTicker.v3.api@'):
                symbol = channel.split('@')[-1].replace('USDT', '')
                book_data = data['data']

                cache_manager.set_book_ticker_cache(symbol, {
                    'symbol': book_data['s'],
                    'bidPrice': book_data['b'],
                    'askPrice': book_data['a']
                })

    async def _ping_handler(self):
        """Отправляет ping для поддержания соединения"""
        while self.running:
            try:
                if self.websocket and not self.websocket.closed:
                    await self.websocket.ping()
                    self.last_ping = time.time()
                await asyncio.sleep(20)
            except Exception as e:
                bot_logger.debug(f"Ошибка ping: {e}")
                break

    async def subscribe_symbol(self, symbol: str, callback: Optional[Callable] = None):
        """Подписывается на данные символа"""
        if callback:
            self.callbacks[symbol] = callback

        # Подписываемся на miniTicker для основных данных
        mini_ticker_channel = f"spot@public.miniTicker.v3.api@{symbol}USDT"

        # Подписываемся на bookTicker для спреда
        book_ticker_channel = f"spot@public.bookTicker.v3.api@{symbol}USDT"

        for channel in [mini_ticker_channel, book_ticker_channel]:
            if channel not in self.subscriptions:
                subscribe_msg = {
                    "method": "SUBSCRIPTION",
                    "params": [channel]
                }

                try:
                    await self.websocket.send(json.dumps(subscribe_msg))
                    self.subscriptions.add(channel)
                    bot_logger.debug(f"Подписка на {channel}")
                except Exception as e:
                    bot_logger.error(f"Ошибка подписки на {channel}: {e}")

    async def unsubscribe_symbol(self, symbol: str):
        """Отписывается от символа"""
        channels_to_remove = [
            f"spot@public.miniTicker.v3.api@{symbol}USDT",
            f"spot@public.bookTicker.v3.api@{symbol}USDT"
        ]

        for channel in channels_to_remove:
            if channel in self.subscriptions:
                unsubscribe_msg = {
                    "method": "UNSUBSCRIPTION", 
                    "params": [channel]
                }

                try:
                    await self.websocket.send(json.dumps(unsubscribe_msg))
                    self.subscriptions.remove(channel)
                except Exception as e:
                    bot_logger.error(f"Ошибка отписки от {channel}: {e}")

        if symbol in self.callbacks:
            del self.callbacks[symbol]

    async def _handle_reconnect(self):
        """Обработка переподключения"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            bot_logger.error("Превышено максимальное количество попыток переподключения")
            self.running = False
            return

        self.reconnect_attempts += 1
        wait_time = min(2 ** self.reconnect_attempts, 60)

        bot_logger.info(f"Переподключение через {wait_time}s (попытка {self.reconnect_attempts})")
        await asyncio.sleep(wait_time)

        if self.running:
            # Сохраняем текущие подписки
            old_subscriptions = self.subscriptions.copy()
            old_callbacks = self.callbacks.copy()

            # Переподключаемся
            await self.connect()

            # Восстанавливаем подписки
            for channel in old_subscriptions:
                if 'miniTicker' in channel:
                    symbol = channel.split('@')[-1].replace('USDT', '')
                    callback = old_callbacks.get(symbol)
                    await self.subscribe_symbol(symbol, callback)

    async def close(self):
        """Закрытие соединения"""
        self.running = False
        if self.websocket:
            await self.websocket.close()
        bot_logger.info("WebSocket закрыт")

# Глобальный экземпляр
ws_client = WebSocketClient()