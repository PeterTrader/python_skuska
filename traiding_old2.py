import logging
import threading
import concurrent.futures
import configparser
import time
from binance.client import Client

# Načítanie konfigurácie
config = configparser.ConfigParser()
config.read('trading.cfg')

# Získanie hodnôt z configu alebo použitie defaultov
MAX_OPEN_ORDERS = int(config.get('BOT', 'MAX_OPEN_ORDERS', fallback='40'))
PAUSE_ON_LOSS_RATIO = float(config.get('BOT', 'PAUSE_ON_LOSS_RATIO', fallback='2.0'))
PAUSE_TIME = int(config.get('BOT', 'PAUSE_TIME', fallback='60'))
TRADE_INTERVAL = int(config.get('BOT', 'TRADE_INTERVAL', fallback='15'))

VOL_HIGH = float(config.get('BOT', 'VOL_HIGH', fallback='100'))
VOL_MED = float(config.get('BOT', 'VOL_MED', fallback='50'))

DEFAULT_PROFIT_LEVELS_HIGH = [float(x) for x in config.get('BOT', 'PROFIT_LEVELS_HIGH', fallback='0.02,0.03,0.04,0.05').split(',')]
DEFAULT_LOSS_LIMIT_HIGH = float(config.get('BOT', 'LOSS_LIMIT_HIGH', fallback='0.01'))

DEFAULT_PROFIT_LEVELS_MED = [float(x) for x in config.get('BOT', 'PROFIT_LEVELS_MED', fallback='0.015,0.02,0.03,0.04').split(',')]
DEFAULT_LOSS_LIMIT_MED = float(config.get('BOT', 'LOSS_LIMIT_MED', fallback='0.008'))

DEFAULT_PROFIT_LEVELS_LOW = [float(x) for x in config.get('BOT', 'PROFIT_LEVELS_LOW', fallback='0.01,0.015,0.02,0.03').split(',')]
DEFAULT_LOSS_LIMIT_LOW = float(config.get('BOT', 'LOSS_LIMIT_LOW', fallback='0.005'))

# Načítanie API kľúčov z configu
api_key = config.get('API', 'API_KEY').strip('"')
api_secret = config.get('API', 'API_SECRET').strip('"')

client = Client(api_key, api_secret)
client.API_URL = 'https://testnet.binance.vision/api'
symbol = 'BTCUSDT'

order_lock = threading.Lock()

# Počítadlá v pamäti (od spustenia skriptu)
total_profit = 0.0
open_orders = []
profit_orders = []
loss_orders = []

# Nastavenie logovania do súboru
logging.basicConfig(
    filename='trading_bot.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    force=True
)

bot_paused = False

# Thread pool executor na správu monitorovacích threadov
MAX_WORKERS = MAX_OPEN_ORDERS
executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)

# Aktuálne limity (na začiatku rovnaké ako default)
current_profit_levels_high = DEFAULT_PROFIT_LEVELS_HIGH[:]
current_loss_limit_high = DEFAULT_LOSS_LIMIT_HIGH
current_profit_levels_med = DEFAULT_PROFIT_LEVELS_MED[:]
current_loss_limit_med = DEFAULT_LOSS_LIMIT_MED
current_profit_levels_low = DEFAULT_PROFIT_LEVELS_LOW[:]
current_loss_limit_low = DEFAULT_LOSS_LIMIT_LOW

def get_real_price(order, cache):
    if float(order['price']) > 0:
        return float(order['price'])
    order_id = order['orderId']
    if order_id in cache:
        return cache[order_id]
    try:
        detail = client.get_order(symbol=symbol, orderId=order_id)
        if 'fills' in detail and detail['fills']:
            price = float(detail['fills'][0]['price'])
            cache[order_id] = price
            time.sleep(0.2)
            return price
    except Exception as e:
        print(f"Chyba pri získavaní ceny z fills: {e}")
        logging.error(f"Chyba pri získavaní ceny z fills: {e}")
    return None

def create_market_order(symbol, side, qty):
    try:
        order = client.create_order(
            symbol=symbol,
            side=side,
            type='MARKET',
            quantity=qty
        )
        print(f"Market {side}: {order}")
        logging.info(f"Market {side}: {order}")
        return order
    except Exception as e:
        print(f"Chyba pri market {side}: {e}")
        logging.error(f"Chyba pri market {side}: {e}")
        return None

def get_volatility(symbol="BTCUSDT", minutes=10):
    try:
        klines = client.get_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_1MINUTE, limit=minutes)
        closes = [float(k[4]) for k in klines]
        if not closes:
            return 0
        return max(closes) - min(closes)
    except Exception as e:
        print(f"Chyba pri získavaní volatility: {e}")
        logging.error(f"Chyba pri získavaní volatility: {e}")
        return 0

def get_dynamic_thresholds(symbol):
    vol = get_volatility(symbol)
    global current_profit_levels_high, current_loss_limit_high
    global current_profit_levels_med, current_loss_limit_med
    global current_profit_levels_low, current_loss_limit_low

    # Dynamická úprava limitov podľa počtu stratových obchodov
    if len(loss_orders) > 5:
        current_profit_levels_high = [x * 0.8 for x in DEFAULT_PROFIT_LEVELS_HIGH]
        current_loss_limit_high = DEFAULT_LOSS_LIMIT_HIGH * 0.8
        current_profit_levels_med = [x * 0.8 for x in DEFAULT_PROFIT_LEVELS_MED]
        current_loss_limit_med = DEFAULT_LOSS_LIMIT_MED * 0.8
        current_profit_levels_low = [x * 0.8 for x in DEFAULT_PROFIT_LEVELS_LOW]
        current_loss_limit_low = DEFAULT_LOSS_LIMIT_LOW * 0.8
    else:
        current_profit_levels_high = DEFAULT_PROFIT_LEVELS_HIGH[:]
        current_loss_limit_high = DEFAULT_LOSS_LIMIT_HIGH
        current_profit_levels_med = DEFAULT_PROFIT_LEVELS_MED[:]
        current_loss_limit_med = DEFAULT_LOSS_LIMIT_MED
        current_profit_levels_low = DEFAULT_PROFIT_LEVELS_LOW[:]
        current_loss_limit_low = DEFAULT_LOSS_LIMIT_LOW

    if vol > VOL_HIGH:
        profit_levels = current_profit_levels_high
        loss_limit = current_loss_limit_high
    elif vol > VOL_MED:
        profit_levels = current_profit_levels_med
        loss_limit = current_loss_limit_med
    else:
        profit_levels = current_profit_levels_low
        loss_limit = current_loss_limit_low

    print(f"Aktuálna volatilita: {vol:.2f} | profit_levels: {profit_levels} | loss_limit: {loss_limit}")
    logging.info(f"Volatilita: {vol:.2f} | profit_levels: {profit_levels} | loss_limit: {loss_limit}")
    return profit_levels, loss_limit

def monitor_order(order, cache):
    order_id = order['orderId']
    open_price = get_real_price(order, cache)
    side = order['side']
    qty = float(order['executedQty'])
    logging.info(f"Spúšťam monitorovanie objednávky: SIDE={side}, QTY={qty}, OPEN_PRICE={open_price}")

    while True:
        profit_levels, loss_limit = get_dynamic_thresholds(symbol)
        try:
            current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
        except Exception as e:
            logging.error(f"Chyba pri získavaní ceny: {e}")
            time.sleep(2)
            continue

        if side == "BUY":
            profit = (current_price - open_price) * qty
            profit_percent = ((current_price - open_price) / open_price) * 100
        else:
            profit = (open_price - current_price) * qty
            profit_percent = ((open_price - current_price) / open_price) * 100

        logging.info(f"Objednávka {side} | Aktuálny profit: {profit_percent:.4f}% | Cena: {current_price}")

        # Profit
        for i, level in enumerate(profit_levels):
            if profit_percent >= level * 100:
                sell_side = "SELL" if side == "BUY" else "BUY"
                result = create_market_order(symbol, sell_side, qty)
                if result:
                    profit_orders.append(order_id)
                    logging.info(f"PROFIT PARTIAL: ID={order_id} PROFIT={profit:.8f} SIDE={side} LEVEL={level*100:.2f}%")
                return

        # Loss
        if abs(profit_percent) >= loss_limit * 100:
            sell_side = "SELL" if side == "BUY" else "BUY"
            result = create_market_order(symbol, sell_side, qty)
            if result:
                loss_orders.append(order_id)
                logging.info(f"LOSS ORDER: ID={order_id} LOSS={profit:.8f} SIDE={side} LIMIT={loss_limit}")
            return

        time.sleep(5)

def main():
    cache = {}
    global bot_paused

    while True:
        if bot_paused:
            logging.info(f"Bot je pozastavený na {PAUSE_TIME} sekúnd.")
            time.sleep(PAUSE_TIME)
            bot_paused = False

        try:
            orders = client.get_open_orders(symbol=symbol)
        except Exception as e:
            logging.error(f"Chyba pri získavaní otvorených objednávok: {e}")
            time.sleep(10)
            continue

        # Monitoruj nové objednávky
        for order in orders:
            order_id = order['orderId']
            if order_id not in open_orders and order_id not in profit_orders and order_id not in loss_orders:
                open_orders.append(order_id)
                executor.submit(monitor_order, order, cache)

        # Pauza ak je veľa stratových objednávok
        if len(loss_orders) > 0 and len(loss_orders) / max(1, len(profit_orders)) > PAUSE_ON_LOSS_RATIO:
            bot_paused = True

        time.sleep(TRADE_INTERVAL)

if __name__ == "__main__":
    main()