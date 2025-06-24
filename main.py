import asyncio
import json
import os
import time
from collections import defaultdict

import aiohttp
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ConversationHandler, MessageHandler, filters, ContextTypes
)

MEXC_WS_URL = "wss://wbs.mexc.com/ws"

VOLUME_THRESHOLD = 1000
SPREAD_THRESHOLD = 0.1
NATR_THRESHOLD = 0.5

TELEGRAM_TOKEN = "8180368589:AAHgiD22KRFzXHTiFkw4n5WPwN3Ho2hA4rA"
TELEGRAM_CHAT_ID = "1090477927"

WATCHLIST_FILE = "watchlist.json"
BOT_RUNNING = False

MENU_KEYBOARD = ReplyKeyboardMarkup([
    ["Запустить бота", "Выключить бота"],
    ["Добавить монету", "Исключить монету"],
    ["Показать список монет"]
], resize_keyboard=True)

ADDING, REMOVING = range(2)

WATCHLIST = set()
ACTIVE_COINS = {}
CANDLE_DATA = {}
DEPTH_DATA = {}
TRADE_COUNTS = defaultdict(int)
TRADE_TIMESTAMPS = defaultdict(list)

ws_task = None
ws_restart_event = asyncio.Event()


def load_watchlist():
    global WATCHLIST
    if os.path.exists(WATCHLIST_FILE):
        WATCHLIST = set([c.upper().replace("_USDT", "") for c in json.load(open(WATCHLIST_FILE, "r", encoding="utf-8"))])
        print(f"📥 Загружен список монет: {', '.join(WATCHLIST)}")
    else:
        WATCHLIST = set()
        print("⚠️ Файл watchlist.json не найден, список пуст.")


def save_watchlist():
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(list(WATCHLIST), f)
    print(f"💾 Сохранен список монет: {', '.join(WATCHLIST)}")


async def send_telegram_message(text, parse_mode="HTML"):
    async with aiohttp.ClientSession() as session:
        async with session.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={
            'chat_id': TELEGRAM_CHAT_ID,
            'text': text,
            'parse_mode': parse_mode
        }) as resp:
            if resp.status == 200:
                data = await resp.json()
                print(f"✉️ Отправлено сообщение в Telegram: {text[:50]}...")
                return data.get("result", {}).get("message_id")
            else:
                print(f"❌ Ошибка отправки сообщения в Telegram: HTTP {resp.status}")
    return None


async def delete_message(message_id):
    async with aiohttp.ClientSession() as session:
        await session.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage", data={
            "chat_id": TELEGRAM_CHAT_ID,
            "message_id": message_id
        })
    print(f"🗑 Удалено сообщение Telegram ID {message_id}")


def calculate_natr_and_change(candle):
    if not candle:
        return None, None
    high = candle['high']
    low = candle['low']
    close = candle['close']
    open_ = candle['open']
    natr = (high - low) / close * 100 if close else 0
    change = (close - open_) / open_ * 100 if open_ else 0
    return natr, change


async def websocket_handler():
    global BOT_RUNNING

    session = aiohttp.ClientSession()
    ws = None

    try:
        print("🔗 Подключаемся к WebSocket...")
        ws = await session.ws_connect(MEXC_WS_URL)
        print("✅ Подключение к WebSocket установлено")

        while BOT_RUNNING:
            if not WATCHLIST:
                print("⚠️ Watchlist пуст, подписка не выполнена")
                await asyncio.sleep(5)
                continue

            # Подписываемся один раз после подключения и после перезапуска
            params = []
            for sym in WATCHLIST:
                symbol = sym.upper() + "USDT"
                params.append(f"spot@public.aggre.deals.v3.api.pb@100ms@{symbol}")
                params.append(f"spot@public.ticker.v3.api.pb@{symbol}")

            subscribe_msg = {
                "method": "SUBSCRIPTION",
                "params": params,
                "id": int(time.time())
            }
            await ws.send_json(subscribe_msg)
            print(f"📋 Подписались на монеты: {', '.join(WATCHLIST)}")

            async for msg in ws:
                if not BOT_RUNNING:
                    print("🔌 BOT_RUNNING выключен, прерываем WebSocket цикл")
                    break

                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)

                    if "dealsList" in data:
                        symbol = data.get("symbol", "").replace("USDT", "")
                        if symbol in WATCHLIST:
                            trades = data["dealsList"]
                            now_ms = int(time.time() * 1000)
                            for t in trades:
                                trade_time = t.get("time", now_ms)
                                TRADE_TIMESTAMPS[symbol].append(trade_time)
                            cutoff = now_ms - 60_000
                            TRADE_TIMESTAMPS[symbol] = [ts for ts in TRADE_TIMESTAMPS[symbol] if ts >= cutoff]
                            TRADE_COUNTS[symbol] = len(TRADE_TIMESTAMPS[symbol])

                    elif "ticker" in data:
                        symbol = data.get("symbol", "").replace("USDT", "")
                        if symbol in WATCHLIST:
                            ticker = data["ticker"]
                            candle = {
                                "open": float(ticker.get("open", 0)),
                                "close": float(ticker.get("close", 0)),
                                "high": float(ticker.get("high", 0)),
                                "low": float(ticker.get("low", 0)),
                                "volume": float(ticker.get("volume", 0))
                            }
                            CANDLE_DATA[symbol] = candle

                            ask = float(ticker.get("ask", 0))
                            bid = float(ticker.get("bid", 0))
                            if bid > 0:
                                spread = (ask - bid) / bid * 100
                                DEPTH_DATA[symbol] = spread

                    # Проверяем активность монет
                    for symbol in WATCHLIST:
                        candle = CANDLE_DATA.get(symbol)
                        spread = DEPTH_DATA.get(symbol)
                        trades = TRADE_COUNTS.get(symbol, 0)
                        if candle and spread is not None:
                            natr, change = calculate_natr_and_change(candle)
                            volume = candle["volume"]
                            active = symbol in ACTIVE_COINS
                            if volume >= VOLUME_THRESHOLD and spread >= SPREAD_THRESHOLD and natr >= NATR_THRESHOLD:
                                if not active:
                                    print(f"🚨 Монета {symbol} стала активной")
                                    msg = (
                                        f"🚨 <b>{symbol}_USDT активен</b>\n"
                                        f"🔄 Изм: {change:.2f}%  🔁 Сделок: {trades}\n"
                                        f"📊 Объём: ${volume:,.2f}  NATR: {natr:.2f}%\n"
                                        f"⇄ Спред: {spread:.2f}%"
                                    )
                                    msg_id = await send_telegram_message(msg)
                                    ACTIVE_COINS[symbol] = {'start': time.time(), 'msg_id': msg_id}
                            else:
                                if active:
                                    duration = time.time() - ACTIVE_COINS[symbol]['start']
                                    msg_id = ACTIVE_COINS[symbol]['msg_id']
                                    if msg_id:
                                        await delete_message(msg_id)
                                    if duration >= 60:
                                        minutes = int(duration // 60)
                                        seconds = int(duration % 60)
                                        msg = f"✅ <b>{symbol}_USDT</b> — активность завершена\n⏱ Длительность: {minutes} мин {seconds} сек"
                                        await send_telegram_message(msg)
                                    print(f"✅ Активность монеты {symbol} завершена (длительность {int(duration)} сек)")
                                    del ACTIVE_COINS[symbol]

                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"❌ Ошибка WebSocket: {msg.data}")
                    break

                # Обработка перезапуска WebSocket при команде
                if ws_restart_event.is_set():
                    print("🔄 Сигнал перезапуска WebSocket получен.")
                    ws_restart_event.clear()
                    break

            # После выхода из async for — закрываем ws и ждем перед переподключением
            await ws.close()
            print("🔌 WebSocket сессия закрыта")

            if BOT_RUNNING:
                print("⏳ Ждем 5 секунд перед переподключением...")
                await asyncio.sleep(5)

    except Exception as e:
        print(f"⚠️ WebSocket исключение: {e}")

    finally:
        if ws and not ws.closed:
            await ws.close()
        await session.close()
        print("🔌 WebSocket сессия полностью закрыта")


async def run_ws_loop():
    global BOT_RUNNING
    while BOT_RUNNING:
        try:
            print("🔄 Запускаем WebSocket цикл...")
            await websocket_handler()
        except Exception as e:
            print(f"⚠️ Ошибка WebSocket в run_ws_loop: {e}")
        if BOT_RUNNING:
            print("⏳ Ждем 5 секунд перед переподключением...")
            await asyncio.sleep(5)


# ——— Telegram handlers ———

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    print(f"🟢 /start от {update.effective_user.username}")
    await update.message.reply_text("Выберите действие:", reply_markup=MENU_KEYBOARD)
    return ConversationHandler.END


async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()

    if text == "запустить бота":
        global BOT_RUNNING, ws_task
        if BOT_RUNNING:
            await update.message.reply_text("Бот уже запущен.", reply_markup=MENU_KEYBOARD)
        else:
            BOT_RUNNING = True
            ws_task = asyncio.create_task(run_ws_loop())
            await update.message.reply_text("Бот запущен.", reply_markup=MENU_KEYBOARD)

    elif text == "выключить бота":
        if not BOT_RUNNING:
            await update.message.reply_text("Бот уже выключен.", reply_markup=MENU_KEYBOARD)
        else:
            BOT_RUNNING = False
            ws_restart_event.set()  # Чтобы прервать ожидание в WS
            if ws_task:
                await ws_task
            await update.message.reply_text("Бот остановлен.", reply_markup=MENU_KEYBOARD)

    elif text == "добавить монету":
        await update.message.reply_text("Введите название монеты (например, BTC):", reply_markup=None)
        return ADDING

    elif text == "исключить монету":
        await update.message.reply_text("Введите название монеты для удаления:", reply_markup=None)
        return REMOVING

    elif text == "показать список монет":
        if WATCHLIST:
            coins = ', '.join(sorted(WATCHLIST))
            await update.message.reply_text(f"Список монет: {coins}", reply_markup=MENU_KEYBOARD)
        else:
            await update.message.reply_text("Список монет пуст.", reply_markup=MENU_KEYBOARD)

    else:
        await update.message.reply_text("Я не знаю, что вы хотите. Пожалуйста, выберите действие из меню.", reply_markup=MENU_KEYBOARD)

    return ConversationHandler.END


async def add_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    coin = update.message.text.strip().upper()
    if coin.endswith("_USDT"):
        coin = coin[:-5]
    if coin in WATCHLIST:
        await update.message.reply_text(f"Монета {coin} уже в списке.", reply_markup=MENU_KEYBOARD)
    else:
        WATCHLIST.add(coin)
        save_watchlist()
        await update.message.reply_text(f"Монета {coin} добавлена в список.", reply_markup=MENU_KEYBOARD)

    # Если бот запущен — перезапускаем WebSocket для подписки на новую монету
    if BOT_RUNNING:
        ws_restart_event.set()

    return ConversationHandler.END


async def remove_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    coin = update.message.text.strip().upper()
    if coin.endswith("_USDT"):
        coin = coin[:-5]
    if coin in WATCHLIST:
        WATCHLIST.remove(coin)
        save_watchlist()
        await update.message.reply_text(f"Монета {coin} удалена из списка.", reply_markup=MENU_KEYBOARD)
        # Удаляем данные об активности, если была
        ACTIVE_COINS.pop(coin, None)
    else:
        await update.message.reply_text(f"Монеты {coin} в списке нет.", reply_markup=MENU_KEYBOARD)

    if BOT_RUNNING:
        ws_restart_event.set()

    return ConversationHandler.END


async def unknown_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Я не знаю, что вы хотите. Пожалуйста, выберите действие из меню.", reply_markup=MENU_KEYBOARD)


def main():
    load_watchlist()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & (~filters.COMMAND), button_handler)],
        states={
            ADDING: [MessageHandler(filters.TEXT & (~filters.COMMAND), add_coin)],
            REMOVING: [MessageHandler(filters.TEXT & (~filters.COMMAND), remove_coin)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), unknown_message))

    print("🤖 Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
