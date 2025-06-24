import requests
import time
import threading
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ConversationHandler, MessageHandler, filters, ContextTypes
import asyncio

MEXC_BASE_URL = "https://api.mexc.com/api/v3"
VOLUME_THRESHOLD = 1000
SPREAD_THRESHOLD = 0.1
NATR_THRESHOLD = 0.5

TELEGRAM_TOKEN = "8180368589:AAHgiD22KRFzXHTiFkw4n5WPwN3Ho2hA4rA"
TELEGRAM_CHAT_ID = "1090477927"

WATCHLIST_FILE = "watchlist.json"
BOT_RUNNING = False
CHECK_BATCH_SIZE = 10
CHECK_BATCH_INTERVAL = 1.5
CHECK_FULL_CYCLE_INTERVAL = 3

# Хранилище активностей
ACTIVE_COINS = {}

def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        return set([c.upper().replace("_USDT", "") for c in json.load(open(WATCHLIST_FILE, "r", encoding="utf-8"))])
    return set()

def save_watchlist():
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(list(WATCHLIST), f)

WATCHLIST = load_watchlist()

def get_candle(symbol, interval='1m'):
    sym = symbol if symbol.endswith("USDT") else symbol + "USDT"
    params = {'symbol': sym, 'interval': interval, 'limit': 1}
    j = requests.get(f"{MEXC_BASE_URL}/klines", params=params, timeout=10).json()
    if not isinstance(j, list) or not j:
        return None
    c = j[0]
    return {
        'open': float(c[1]), 'close': float(c[4]),
        'high': float(c[2]), 'low': float(c[3]),
        'volume': float(c[7])
    }

def get_depth(symbol):
    sym = symbol if symbol.endswith("USDT") else symbol + "USDT"
    params = {'symbol': sym, 'limit': 1}
    j = requests.get(f"{MEXC_BASE_URL}/depth", params=params, timeout=10).json()
    bids, asks = j.get('bids'), j.get('asks')
    if not bids or not asks:
        return None
    bid, ask = float(bids[0][0]), float(asks[0][0])
    return (ask - bid) / bid * 100

def get_trade_count(symbol):
    sym = symbol if symbol.endswith("USDT") else symbol + "USDT"
    params = {'symbol': sym, 'limit': 1000}
    trades = requests.get(f"{MEXC_BASE_URL}/trades", params=params, timeout=10).json()
    one_min_ago = int(time.time() * 1000) - 60_000
    return sum(1 for t in trades if t.get('time', 0) >= one_min_ago)

def check_coin(symbol):
    candle = get_candle(symbol)
    if not candle: return None
    spread = get_depth(symbol)
    if spread is None: return None

    natr = (candle['high'] - candle['low']) / candle['close'] * 100
    volume = candle['volume']
    change = (candle['close'] - candle['open']) / candle['open'] * 100
    trades = get_trade_count(symbol)

    if volume >= VOLUME_THRESHOLD and spread >= SPREAD_THRESHOLD and natr >= NATR_THRESHOLD:
        return {
            'symbol': symbol,
            'volume': volume,
            'spread': spread,
            'natr': natr,
            'change': change,
            'trades': trades
        }
    return None

async def send_telegram_message(text, parse_mode="HTML"):
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': parse_mode
    })
    if r.ok:
        return r.json().get("result", {}).get("message_id")
    return None

async def delete_message(message_id):
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage", data={
        "chat_id": TELEGRAM_CHAT_ID,
        "message_id": message_id
    })

def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

async def async_bot_loop():
    global BOT_RUNNING
    while BOT_RUNNING:
        if not WATCHLIST:
            await asyncio.sleep(CHECK_FULL_CYCLE_INTERVAL)
            continue

        for batch in chunks(list(WATCHLIST), CHECK_BATCH_SIZE):
            tasks = []
            with ThreadPoolExecutor(max_workers=CHECK_BATCH_SIZE) as pool:
                futures = {pool.submit(check_coin, coin): coin for coin in batch}
                for f in as_completed(futures):
                    symbol = futures[f]
                    result = f.result()

                    now = time.time()
                    active = symbol in ACTIVE_COINS

                    if result and not active:
                        msg = (
                            f"🚨 <b>{symbol}_USDT активен</b>\n"
                            f"🔄 Изм: {result['change']:.2f}%  🔁 Сделок: {result['trades']}\n"
                            f"📊 Объём: ${result['volume']:,.2f}  NATR: {result['natr']:.2f}%\n"
                            f"⇄ Спред: {result['spread']:.2f}%"
                        )
                        msg_id = await send_telegram_message(msg)
                        ACTIVE_COINS[symbol] = {'start': now, 'msg_id': msg_id}

                    elif not result and active:
                        duration = now - ACTIVE_COINS[symbol]['start']
                        msg_id = ACTIVE_COINS[symbol]['msg_id']
                        if msg_id:
                            await delete_message(msg_id)

                        if duration >= 60:
                            minutes = int(duration // 60)
                            seconds = int(duration % 60)
                            msg = f"✅ <b>{symbol}_USDT</b> — активность завершена\n⏱ Длительность: {minutes} мин {seconds} сек"
                            await send_telegram_message(msg)
                        del ACTIVE_COINS[symbol]

            await asyncio.sleep(CHECK_BATCH_INTERVAL)
        await asyncio.sleep(CHECK_FULL_CYCLE_INTERVAL)

def start_bot_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(async_bot_loop())

# ——— Telegram меню ———
MENU_KEYBOARD = ReplyKeyboardMarkup([
    ["Запустить бота", "Выключить бота"],
    ["Добавить монету", "Исключить монету"],
    ["Показать список монет"]
], resize_keyboard=True)

ADDING, REMOVING = range(2)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выберите действие:", reply_markup=MENU_KEYBOARD)

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global BOT_RUNNING
    t = update.message.text
    if t == "Запустить бота":
        if not BOT_RUNNING:
            BOT_RUNNING = True
            threading.Thread(target=start_bot_loop, daemon=True).start()
            await update.message.reply_text("Бот запущен.", reply_markup=MENU_KEYBOARD)
        else:
            await update.message.reply_text("Бот уже работает.", reply_markup=MENU_KEYBOARD)
    elif t == "Выключить бота":
        BOT_RUNNING = False
        await update.message.reply_text("Бот остановлен.", reply_markup=MENU_KEYBOARD)
    elif t == "Добавить монету":
        await update.message.reply_text("Введите символ (например BTC):")
        return ADDING
    elif t == "Исключить монету":
        await update.message.reply_text("Введите символ:")
        return REMOVING
    elif t == "Показать список монет":
        text = "\n".join(WATCHLIST) or "Список пуст."
        await update.message.reply_text(text, reply_markup=MENU_KEYBOARD)
    else:
        await update.message.reply_text("Выберите из меню:", reply_markup=MENU_KEYBOARD)
    return ConversationHandler.END

async def add_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    coin = update.message.text.upper().strip().replace("_USDT", "")
    if get_candle(coin):
        WATCHLIST.add(coin)
        save_watchlist()
        await update.message.reply_text(f"✅ {coin} добавлена.", parse_mode='HTML', reply_markup=MENU_KEYBOARD)
    else:
        await update.message.reply_text(f"❌ {coin}: не найдена.", parse_mode='HTML', reply_markup=MENU_KEYBOARD)
    return ConversationHandler.END

async def remove_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    coin = update.message.text.upper().strip().replace("_USDT", "")
    if coin in WATCHLIST:
        WATCHLIST.remove(coin)
        save_watchlist()
        await update.message.reply_text(f"{coin} удалена.", reply_markup=MENU_KEYBOARD)
    else:
        await update.message.reply_text(f"{coin} не в списке.", reply_markup=MENU_KEYBOARD)
    return ConversationHandler.END

# ————————————
if __name__ == '__main__':
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler)],
        states={ADDING: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_coin)],
                REMOVING: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_coin)]},
        fallbacks=[], per_message=False
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    print("🚀 Бот запущен..")
    app.run_polling()
