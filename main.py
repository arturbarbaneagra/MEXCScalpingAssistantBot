import requests
import time
import threading
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, ConversationHandler, MessageHandler, filters, ContextTypes
import asyncio
from flask import Flask
from threading import Thread

# Конфигурация
MEXC_BASE_URL = "https://api.mexc.com/api/v3"
WATCHLIST_FILE = "watchlist.json"
CONFIG_FILE = "config.json"
TELEGRAM_TOKEN = "8180368589:AAHgiD22KRFzXHTiFkw4n5WPwN3Ho2hA4rA"
TELEGRAM_CHAT_ID = "1090477927"

# Параметры по умолчанию
DEFAULT_CONFIG = {
    'VOLUME_THRESHOLD': 1000,
    'SPREAD_THRESHOLD': 0.1,
    'NATR_THRESHOLD': 0.5,
    'CHECK_BATCH_SIZE': 10,
    'CHECK_BATCH_INTERVAL': 1.0,
    'CHECK_FULL_CYCLE_INTERVAL': 2,
    'INACTIVITY_TIMEOUT': 30,
    'COIN_DATA_DELAY': 0.5,
    'MONITORING_UPDATE_INTERVAL': 15,
    'MAX_API_REQUESTS_PER_SECOND': 8,
    'MESSAGE_RATE_LIMIT': 20
}

# Глобальные переменные
BOT_RUNNING = False
BOT_MODE = None  # 'notification' или 'monitoring'
ACTIVE_COINS = {}
monitoring_message_id = None
last_message_time = 0
WATCHLIST = set()

# Загрузка конфигурации
def load_config():
    # Создаем файл с настройками по умолчанию, если его нет
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(DEFAULT_CONFIG, f)
        return DEFAULT_CONFIG

    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            # Обновляем только существующие ключи
            return {**DEFAULT_CONFIG, **{k: v for k, v in config.items() if k in DEFAULT_CONFIG}}
    except (json.JSONDecodeError, IOError):
        # Если файл поврежден, создаем заново
        with open(CONFIG_FILE, 'w') as f:
            json.dump(DEFAULT_CONFIG, f)
        return DEFAULT_CONFIG

def save_config():
    with open(CONFIG_FILE, 'w') as f:
        json.dump({k: v for k, v in globals().items() if k in DEFAULT_CONFIG}, f)

# Загружаем конфиг при старте
config = load_config()
globals().update(config)

def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        return set([c.upper().replace("_USDT", "") for c in json.load(open(WATCHLIST_FILE, "r", encoding="utf-8"))])
    return set()

def save_watchlist():
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(list(WATCHLIST), f)

WATCHLIST = load_watchlist()

# ——— API функции ———
def get_candle(symbol, interval='1m'):
    sym = symbol if symbol.endswith("USDT") else symbol + "USDT"
    params = {'symbol': sym, 'interval': interval, 'limit': 1}
    try:
        response = requests.get(f"{MEXC_BASE_URL}/klines", params=params, timeout=5)
        response.raise_for_status()
        j = response.json()
        if not isinstance(j, list) or not j:
            return None
        c = j[0]
        return {
            'open': float(c[1]), 'close': float(c[4]),
            'high': float(c[2]), 'low': float(c[3]),
            'volume': float(c[7])
        }
    except Exception as e:
        print(f"Error getting candle for {symbol}: {e}")
        return None

def get_depth(symbol):
    sym = symbol if symbol.endswith("USDT") else symbol + "USDT"
    params = {'symbol': sym, 'limit': 1}
    try:
        response = requests.get(f"{MEXC_BASE_URL}/depth", params=params, timeout=5)
        response.raise_for_status()
        j = response.json()
        bids, asks = j.get('bids'), j.get('asks')
        if not bids or not asks:
            return None
        bid, ask = float(bids[0][0]), float(asks[0][0])
        return (ask - bid) / bid * 100
    except Exception as e:
        print(f"Error getting depth for {symbol}: {e}")
        return None

def get_trade_count(symbol):
    sym = symbol if symbol.endswith("USDT") else symbol + "USDT"
    params = {'symbol': sym, 'limit': 1000}
    try:
        response = requests.get(f"{MEXC_BASE_URL}/trades", params=params, timeout=5)
        response.raise_for_status()
        trades = response.json()
        one_min_ago = int(time.time() * 1000) - 60_000
        return sum(1 for t in trades if t.get('time', 0) >= one_min_ago)
    except Exception as e:
        print(f"Error getting trades for {symbol}: {e}")
        return 0

def get_coin_data(symbol):
    """Получает все данные по монете"""
    time.sleep(COIN_DATA_DELAY)

    candle = get_candle(symbol)
    if not candle:
        return None

    spread = get_depth(symbol)
    if spread is None:
        spread = 0

    natr = (candle['high'] - candle['low']) / candle['close'] * 100 if candle['close'] != 0 else 0
    volume = candle['volume']
    change = (candle['close'] - candle['open']) / candle['open'] * 100 if candle['open'] != 0 else 0
    trades = get_trade_count(symbol)

    return {
        'symbol': symbol,
        'volume': volume,
        'spread': spread,
        'natr': natr,
        'change': change,
        'trades': trades,
        'active': (volume >= VOLUME_THRESHOLD and spread >= SPREAD_THRESHOLD and natr >= NATR_THRESHOLD)
    }

# ——— Telegram функции ———
async def send_telegram_message(text, parse_mode="HTML", reply_markup=None):
    global last_message_time

    current_time = time.time()
    if current_time - last_message_time < 3:
        await asyncio.sleep(3 - (current_time - last_message_time))

    try:
        data = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': text,
            'parse_mode': parse_mode
        }
        if reply_markup:
            data['reply_markup'] = reply_markup

        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                         data=data, timeout=5)
        if r.ok:
            last_message_time = time.time()
            return r.json().get("result", {}).get("message_id")
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
    return None

async def edit_message(message_id, text, parse_mode="HTML", reply_markup=None):
    try:
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode
        }
        if reply_markup:
            data['reply_markup'] = reply_markup

        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText", 
                     data=data, timeout=5)
    except Exception as e:
        print(f"Error editing Telegram message: {e}")

async def delete_message(message_id):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage", data={
            "chat_id": TELEGRAM_CHAT_ID,
            "message_id": message_id
        }, timeout=5)
    except Exception as e:
        print(f"Error deleting Telegram message: {e}")

# ——— Основные функции бота ———
def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

async def notification_mode_loop():
    """Цикл для режима уведомлений"""
    global BOT_RUNNING, ACTIVE_COINS

    while BOT_RUNNING and BOT_MODE == 'notification':
        if not WATCHLIST:
            await asyncio.sleep(CHECK_FULL_CYCLE_INTERVAL)
            continue

        for batch in chunks(list(WATCHLIST), CHECK_BATCH_SIZE):
            if not BOT_RUNNING or BOT_MODE != 'notification':
                break

            tasks = []
            with ThreadPoolExecutor(max_workers=min(CHECK_BATCH_SIZE, 10)) as pool:
                futures = {pool.submit(get_coin_data, coin): coin for coin in batch}
                for f in as_completed(futures):
                    symbol = futures[f]
                    result = f.result()
                    if not result:
                        continue

                    now = time.time()
                    active = symbol in ACTIVE_COINS

                    if result['active']:
                        if not active:
                            # Новая активная монета
                            msg = (
                                f"🚨 <b>{symbol}_USDT активен</b>\n"
                                f"🔄 Изм: {result['change']:.2f}%  🔁 Сделок: {result['trades']}\n"
                                f"📊 Объём: ${result['volume']:,.2f}  NATR: {result['natr']:.2f}%\n"
                                f"⇄ Спред: {result['spread']:.2f}%"
                            )
                            msg_id = await send_telegram_message(msg)
                            if msg_id:
                                ACTIVE_COINS[symbol] = {
                                    'start': now,
                                    'last_active': now,
                                    'msg_id': msg_id,
                                    'data': result
                                }
                        else:
                            # Обновляем данные по активной монете
                            ACTIVE_COINS[symbol]['last_active'] = now
                            ACTIVE_COINS[symbol]['data'] = result

                            msg = (
                                f"🚨 <b>{symbol}_USDT активен</b>\n"
                                f"🔄 Изм: {result['change']:.2f}%  🔁 Сделок: {result['trades']}\n"
                                f"📊 Объём: ${result['volume']:,.2f}  NATR: {result['natr']:.2f}%\n"
                                f"⇄ Спред: {result['spread']:.2f}%"
                            )
                            await edit_message(ACTIVE_COINS[symbol]['msg_id'], msg)
                    elif active:
                        # Проверяем, не прошло ли время неактивности
                        if now - ACTIVE_COINS[symbol]['last_active'] > INACTIVITY_TIMEOUT:
                            # Удаляем сообщение
                            msg_id = ACTIVE_COINS[symbol]['msg_id']
                            if msg_id:
                                await delete_message(msg_id)

                            # Отправляем сообщение о завершении активности
                            duration = now - ACTIVE_COINS[symbol]['start']
                            if duration >= 60:
                                duration_min = int(duration // 60)
                                duration_sec = int(duration % 60)
                                end_msg = (
                                    f"✅ <b>{symbol}_USDT завершил активность</b>\n"
                                    f"⏱ Длительность: {duration_min} мин {duration_sec} сек"
                                )
                                await send_telegram_message(end_msg)

                            del ACTIVE_COINS[symbol]

            await asyncio.sleep(CHECK_BATCH_INTERVAL)
        await asyncio.sleep(CHECK_FULL_CYCLE_INTERVAL)

async def monitoring_mode_loop():
    """Цикл для режима мониторинга"""
    global BOT_RUNNING, monitoring_message_id

    initial_text = "🔄 Собираю данные для мониторинга..."
    monitoring_message_id = await send_telegram_message(initial_text)

    while BOT_RUNNING and BOT_MODE == 'monitoring':
        if not WATCHLIST:
            await asyncio.sleep(MONITORING_UPDATE_INTERVAL)
            continue

        results = []
        failed_coins = []

        for batch in chunks(sorted(WATCHLIST), CHECK_BATCH_SIZE):
            if not BOT_RUNNING or BOT_MODE != 'monitoring':
                break

            batch_results = []
            for symbol in batch:
                try:
                    data = get_coin_data(symbol)
                    if data:
                        batch_results.append(data)
                    else:
                        failed_coins.append(symbol)
                except Exception as e:
                    print(f"Ошибка при получении данных для {symbol}: {e}")
                    failed_coins.append(symbol)
                await asyncio.sleep(COIN_DATA_DELAY)

            results.extend(batch_results)
            await asyncio.sleep(CHECK_BATCH_INTERVAL)

        if not results:
            text = "Не удалось получить данные ни по одной монете."
            if monitoring_message_id:
                await edit_message(monitoring_message_id, text)
            else:
                monitoring_message_id = await send_telegram_message(text)
            await asyncio.sleep(MONITORING_UPDATE_INTERVAL)
            continue

        results.sort(key=lambda x: x['volume'], reverse=True)

        report_parts = ["<b>📊 Режим мониторинга (обновляется автоматически):</b>\n"]
        report_parts.append(f"<i>Фильтры: Объём ≥${VOLUME_THRESHOLD}, Спред ≥{SPREAD_THRESHOLD}%, NATR ≥{NATR_THRESHOLD}%</i>")

        if failed_coins:
            report_parts.append(f"\n⚠ Не удалось получить данные для: {', '.join(failed_coins)}\n")

        max_coins_to_show = 20
        for coin in results[:max_coins_to_show]:
            status = "🟢 АКТИВНА" if coin['active'] else "🔴 НЕАКТИВНА"
            coin_info = (
                f"\n<b>{coin['symbol']}_USDT</b> {status}\n"
                f"📊 Объём: ${coin['volume']:,.2f}\n"
                f"🔄 Изм: {coin['change']:.2f}%\n"
                f"⇄ Спред: {coin['spread']:.2f}%\n"
                f"📈 NATR: {coin['natr']:.2f}%\n"
                f"🔁 Сделок: {coin['trades']}"
            )
            report_parts.append(coin_info)

        if len(results) > max_coins_to_show:
            report_parts.append(f"\n... и ещё {len(results) - max_coins_to_show} монет")

        full_message = "\n".join(report_parts)
        if len(full_message) > 4000:
            full_message = full_message[:4000] + "\n... (сообщение обрезано)"

        if monitoring_message_id:
            await edit_message(monitoring_message_id, full_message)
        else:
            monitoring_message_id = await send_telegram_message(full_message)

        await asyncio.sleep(MONITORING_UPDATE_INTERVAL)

    if monitoring_message_id:
        await delete_message(monitoring_message_id)
        monitoring_message_id = None

def start_bot_loop():
    """Запускает соответствующий цикл в зависимости от режима"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    if BOT_MODE == 'notification':
        loop.run_until_complete(notification_mode_loop())
    elif BOT_MODE == 'monitoring':
        loop.run_until_complete(monitoring_mode_loop())

# ——— Telegram Handlers ———
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await show_main_menu(update)

async def show_main_menu(update: Update, text=None):
    reply_text = text or "Выберите действие:"
    await update.message.reply_text(
        reply_text,
        reply_markup=MAIN_MENU_KEYBOARD
    )

async def show_settings_menu(update: Update):
    current_settings = (
        f"Текущие настройки:\n"
        f"📊 Минимальный объём: ${VOLUME_THRESHOLD}\n"
        f"⇄ Минимальный спред: {SPREAD_THRESHOLD}%\n"
        f"📈 Минимальный NATR: {NATR_THRESHOLD}%"
    )
    await update.message.reply_text(
        current_settings,
        reply_markup=SETTINGS_KEYBOARD
    )

# Клавиатуры
MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup([
    ["🔔 Режим уведомлений", "📊 Режим мониторинга"],
    ["➕ Добавить монету", "➖ Исключить монету"],
    ["📋 Список монет", "⚙ Настройки"],
    ["🛑 Остановить бота"]
], resize_keyboard=True)

SETTINGS_KEYBOARD = ReplyKeyboardMarkup([
    ["📊 Изменить объём", "⇄ Изменить спред"],
    ["📈 Изменить NATR", "🔙 Назад"]
], resize_keyboard=True)

BACK_KEYBOARD = ReplyKeyboardMarkup([
    ["🔙 Назад"]
], resize_keyboard=True)

# Состояния
ADDING, REMOVING = range(2)
SETTING_VOLUME, SETTING_SPREAD, SETTING_NATR = range(3, 6)

async def stop_current_mode():
    """Останавливает текущий режим с корректным завершением"""
    global BOT_RUNNING, ACTIVE_COINS, monitoring_message_id

    if BOT_RUNNING:
        BOT_RUNNING = False
        await asyncio.sleep(2)

        if BOT_MODE == 'monitoring' and monitoring_message_id:
            await delete_message(monitoring_message_id)
            monitoring_message_id = None
        elif BOT_MODE == 'notification':
            ACTIVE_COINS = {}

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global BOT_RUNNING, BOT_MODE, ACTIVE_COINS, monitoring_message_id

    text = update.message.text

    if text == "🔔 Режим уведомлений":
        if BOT_RUNNING and BOT_MODE == 'notification':
            await update.message.reply_text("Бот уже работает в режиме уведомлений.", reply_markup=MAIN_MENU_KEYBOARD)
            return ConversationHandler.END

        await stop_current_mode()
        BOT_MODE = 'notification'
        BOT_RUNNING = True
        ACTIVE_COINS = {}
        threading.Thread(target=start_bot_loop, daemon=True).start()

        await update.message.reply_text(
            "✅ Бот запущен в режиме уведомлений. Будут приходить уведомления только об активных монетах.",
            reply_markup=MAIN_MENU_KEYBOARD
        )

    elif text == "📊 Режим мониторинга":
        if BOT_RUNNING and BOT_MODE == 'monitoring':
            await update.message.reply_text("Бот уже работает в режиме мониторинга.", reply_markup=MAIN_MENU_KEYBOARD)
            return ConversationHandler.END

        await stop_current_mode()
        BOT_MODE = 'monitoring'
        BOT_RUNNING = True
        threading.Thread(target=start_bot_loop, daemon=True).start()

        await update.message.reply_text(
            "✅ Бот запущен в режиме мониторинга. Будет отображаться информация по всем монетам из списка.",
            reply_markup=MAIN_MENU_KEYBOARD
        )

    elif text == "🛑 Остановить бота":
        await stop_current_mode()
        await update.message.reply_text("Бот остановлен.", reply_markup=MAIN_MENU_KEYBOARD)

    elif text == "➕ Добавить монету":
        await stop_current_mode()
        await update.message.reply_text(
            "Введите символ монеты для добавления (например, BTC или BTC_USDT):",
            reply_markup=BACK_KEYBOARD
        )
        return ADDING

    elif text == "➖ Исключить монету":
        await stop_current_mode()
        await update.message.reply_text(
            "Введите символ монеты для исключения:",
            reply_markup=BACK_KEYBOARD
        )
        return REMOVING

    elif text == "📋 Список монет":
        await stop_current_mode()
        text = "\n".join(sorted(WATCHLIST)) or "Список пуст."
        await update.message.reply_text(
            f"📋 Список отслеживаемых монет:\n{text}",
            reply_markup=MAIN_MENU_KEYBOARD
        )

    elif text == "⚙ Настройки":
        await stop_current_mode()
        await show_settings_menu(update)

    elif text == "📊 Изменить объём":
        await update.message.reply_text(
            f"Текущий минимальный объём: ${VOLUME_THRESHOLD}\n"
            "Введите новое значение (в долларах):",
            reply_markup=BACK_KEYBOARD
        )
        return SETTING_VOLUME

    elif text == "⇄ Изменить спред":
        await update.message.reply_text(
            f"Текущий минимальный спред: {SPREAD_THRESHOLD}%\n"
            "Введите новое значение (в процентах):",
            reply_markup=BACK_KEYBOARD
        )
        return SETTING_SPREAD

    elif text == "📈 Изменить NATR":
        await update.message.reply_text(
            f"Текущий минимальный NATR: {NATR_THRESHOLD}%\n"
            "Введите новое значение (в процентах):",
            reply_markup=BACK_KEYBOARD
        )
        return SETTING_NATR

    elif text == "🔙 Назад":
        await show_main_menu(update)
        return ConversationHandler.END

    return ConversationHandler.END

async def add_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    coin = update.message.text.upper().strip().replace("_USDT", "")

    if coin == "🔙 НАЗАД":
        await show_main_menu(update)
        return ConversationHandler.END

    try:
        candle = get_candle(coin)
        depth = get_depth(coin)

        if not candle or not depth:
            await update.message.reply_text(
                f"❌ {coin}_USDT не найдена или не торгуется.",
                reply_markup=MAIN_MENU_KEYBOARD
            )
            return ConversationHandler.END

        WATCHLIST.add(coin)
        save_watchlist()
        await update.message.reply_text(
            f"✅ {coin}_USDT добавлена в список.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Ошибка при проверке {coin}_USDT: {str(e)}",
            reply_markup=MAIN_MENU_KEYBOARD
        )

    return ConversationHandler.END

async def remove_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    coin = update.message.text.upper().strip().replace("_USDT", "")

    if coin == "🔙 НАЗАД":
        await show_main_menu(update)
        return ConversationHandler.END

    if coin in WATCHLIST:
        WATCHLIST.remove(coin)
        save_watchlist()
        await update.message.reply_text(
            f"✅ {coin} удалена из списка.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
    else:
        await update.message.reply_text(
            f"❌ {coin} не в списке.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
    return ConversationHandler.END

async def set_volume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global VOLUME_THRESHOLD

    text = update.message.text

    if text == "🔙 Назад":
        await show_settings_menu(update)
        return ConversationHandler.END

    try:
        new_value = float(text)
        if new_value <= 0:
            raise ValueError("Значение должно быть больше 0")

        VOLUME_THRESHOLD = new_value
        save_config()
        await update.message.reply_text(
            f"✅ Минимальный объём изменён на ${new_value}",
            reply_markup=MAIN_MENU_KEYBOARD
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат. Введите число (например: 1000)",
            reply_markup=BACK_KEYBOARD
        )
        return SETTING_VOLUME

    return ConversationHandler.END

async def set_spread(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global SPREAD_THRESHOLD

    text = update.message.text

    if text == "🔙 Назад":
        await show_settings_menu(update)
        return ConversationHandler.END

    try:
        new_value = float(text)
        if new_value <= 0:
            raise ValueError("Значение должно быть больше 0")

        SPREAD_THRESHOLD = new_value
        save_config()
        await update.message.reply_text(
            f"✅ Минимальный спред изменён на {new_value}%",
            reply_markup=MAIN_MENU_KEYBOARD
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат. Введите число (например: 0.1)",
            reply_markup=BACK_KEYBOARD
        )
        return SETTING_SPREAD

    return ConversationHandler.END

async def set_natr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global NATR_THRESHOLD

    text = update.message.text

    if text == "🔙 Назад":
        await show_settings_menu(update)
        return ConversationHandler.END

    try:
        new_value = float(text)
        if new_value <= 0:
            raise ValueError("Значение должно быть больше 0")

        NATR_THRESHOLD = new_value
        save_config()
        await update.message.reply_text(
            f"✅ Минимальный NATR изменён на {new_value}%",
            reply_markup=MAIN_MENU_KEYBOARD
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат. Введите число (например: 0.5)",
            reply_markup=BACK_KEYBOARD
        )
        return SETTING_NATR

    return ConversationHandler.END

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

if __name__ == '__main__':
    keep_alive()  # Запускаем Flask-сервер в отдельном потоке

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler)],
        states={
            ADDING: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_coin)],
            REMOVING: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_coin)],
            SETTING_VOLUME: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_volume)],
            SETTING_SPREAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_spread)],
            SETTING_NATR: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_natr)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)

    print("🚀 Бот запущен...")
    app.run_polling()