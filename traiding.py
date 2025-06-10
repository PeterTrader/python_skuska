import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException
import time
import threading
import concurrent.futures
import configparser
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import numpy as np
import sys

# --- KONFIGURÁCIA ---
config_file = sys.argv[1] if len(sys.argv) > 1 else "trading.cfg"
config = configparser.ConfigParser()
config.read(config_file)

api_config = configparser.ConfigParser()
api_config.read('api.cfg')

api_key = api_config.get('API', 'API_KEY').strip('"')
api_secret = api_config.get('API', 'API_SECRET').strip('"')

MAX_OPEN_ORDERS = int(config.get('BOT', 'MAX_OPEN_ORDERS', fallback='5'))
PAUSE_ON_LOSS_RATIO = float(config.get('BOT', 'PAUSE_ON_LOSS_RATIO', fallback='1.0'))
PAUSE_TIME = int(config.get('BOT', 'PAUSE_TIME', fallback='60'))
TRADE_INTERVAL = int(config.get('BOT', 'TRADE_INTERVAL', fallback='15'))
SYMBOL = config.get('BOT', 'SYMBOL', fallback='BTCUSDT')
symbol = SYMBOL
TRADE_QTY = float(config.get('BOT', 'TRADE_QTY', fallback='0.0002'))
ORDER_TYPE = config.get('BOT', 'ORDER_TYPE', fallback='MARKET')
VOL_HIGH = float(config.get('BOT', 'VOL_HIGH', fallback='100'))
VOL_MED = float(config.get('BOT', 'VOL_MED', fallback='50'))
VOL_LOW = float(config.get('BOT', 'VOL_LOW', fallback='20'))

PROFIT_LEVELS_HIGH = [float(x) for x in config.get('BOT', 'PROFIT_LEVELS_HIGH', fallback='0.01,0.02,0.03,0.04').split(',')]
PROFIT_LEVELS_MED = [float(x) for x in config.get('BOT', 'PROFIT_LEVELS_MED', fallback='0.01,0.02,0.03,0.04').split(',')]
PROFIT_LEVELS_LOW = [float(x) for x in config.get('BOT', 'PROFIT_LEVELS_LOW', fallback='0.01,0.02,0.03,0.04').split(',')]
LOSS_LIMIT_HIGH = float(config.get('BOT', 'LOSS_LIMIT_HIGH', fallback='0.01'))
LOSS_LIMIT_MED = float(config.get('BOT', 'LOSS_LIMIT_MED', fallback='0.01'))
LOSS_LIMIT_LOW = float(config.get('BOT', 'LOSS_LIMIT_LOW', fallback='0.01'))
MAX_ORDER_LIFETIME = int(config.get('BOT', 'MAX_ORDER_LIFETIME', fallback='0'))
loss_thresholds = [int(x) for x in config.get('BOT', 'TRAILING_OFFSET_THRESHOLDS', fallback='2,5,10').split(',')]
loss_multipliers = [float(x) for x in config.get('BOT', 'TRAILING_OFFSET_MULTIPLIERS', fallback='0.85,0.7,0.5').split(',')]
profit_thresholds = [int(x) for x in config.get('BOT', 'TRAILING_OFFSET_PROFIT_THRESHOLDS', fallback='2,5,10').split(',')]
profit_multipliers = [float(x) for x in config.get('BOT', 'TRAILING_OFFSET_PROFIT_MULTIPLIERS', fallback='1.1,1.3,1.5').split(',')]
EMA_PERIOD = int(config.get('BOT', 'EMA_PERIOD', fallback='20'))


def safe_client_init(api_key, api_secret, max_retries=5):
    for i in range(max_retries):
        try:
            client = Client(api_key, api_secret)
            # Nepoužívaj self.ping() v __init__!
            return client
        except BinanceAPIException as e:
            if e.code == -1003:
                wait = 30 * (i + 1)
                print(f"API limit pri inicializácii, čakám {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise Exception("Nepodarilo sa inicializovať Binance klienta kvôli API limitom.")

client = safe_client_init(api_key, api_secret)

order_lock = threading.Lock()

# --- GLOBÁLNE PREMENNÉ ---
open_orders = []
order_open_time = {}
last_side = "SELL"
last_trade_time = 0
trade_interval = TRADE_INTERVAL
executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
profit_orders = []
loss_orders = []
total_profit = 0.0
bot_paused = False
last_sync = time.time()
last_monitor = time.time()
BUY_SELL_RATIO = int(config.get('BOT', 'BUY_SELL_RATIO', fallback='4'))
trade_counter = 0
dynamic_buy_sell_ratio = BUY_SELL_RATIO  # dynamická hodnota

# Nastav interval hlavného cyklu (napr. 120 sekúnd alebo viac podľa API limitu)
MAIN_LOOP_INTERVAL = 120  # sekúnd (prispôsob podľa potreby)

# --- POMOCNÉ FUNKCIE ---

def get_step_size_once(symbol):
    info = client.get_symbol_info(symbol)
    for f in info['filters']:
        if f['filterType'] == 'LOT_SIZE':
            return f['stepSize']
    return "0.00001"

STEP_SIZE = get_step_size_once(symbol)

def format_quantity(qty, step_size=STEP_SIZE):
    return str(Decimal(qty).quantize(Decimal(step_size), rounding=ROUND_DOWN))

def get_price_filter(symbol):
    info = client.get_symbol_info(symbol)
    tick_size = min_price = max_price = None
    for f in info['filters']:
        if f['filterType'] == 'PRICE_FILTER':
            tick_size = float(f['tickSize'])
            min_price = float(f['minPrice'])
            max_price = float(f['maxPrice'])
            break
    return tick_size, min_price, max_price

def create_market_order(symbol, side, qty):
    try:
        qty = format_quantity(qty, STEP_SIZE)
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

def create_order(symbol, side, qty):
    print(f"Vstupujem do create_order: {side}, qty: {qty}")
    qty = format_quantity(qty, STEP_SIZE)
    current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
    tick_size, min_price, max_price = get_price_filter(symbol)
    order_value = float(qty) * current_price

    def create_order(symbol, side, qty):
        print(f"Vstupujem do create_order: {side}, qty: {qty}")
        qty = format_quantity(qty, STEP_SIZE)
        current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
        tick_size, min_price, max_price = get_price_filter(symbol)
        order_value = float(qty) * current_price

    if side == "BUY":
        usdc_balance = float(client.get_asset_balance(asset='USDC')['free'])
        price = float(client.get_symbol_ticker(symbol=symbol)['price'])
        needed = float(qty) * price
        if usdc_balance < needed:
            print(f"\033[91mNedostatok USDC na BUY! Máš {usdc_balance}, potrebuješ {needed}\033[0m")
            return None
    elif side == "SELL":
        btc_balance = float(client.get_asset_balance(asset='BTC')['free'])
        if btc_balance < float(qty):
            print(f"\033[91mNedostatok BTC na SELL! Máš {btc_balance:.8f}, potrebuješ {float(qty):.8f}\033[0m")
            return None
        
    print(f"Diagnostika: LIMIT {side}, množstvo: {qty}, cena: {current_price}, hodnota objednávky: {order_value:.2f} USDC")
    if ORDER_TYPE == "MARKET":
        try:
            print("Volám client.create_order (MARKET)...")
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
            import traceback
            traceback.print_exc()
            logging.error(f"Chyba pri market {side}: {e}")
            return None
    elif ORDER_TYPE == "LIMIT":
        if side == "BUY":
            limit_price = Decimal(current_price - tick_size).quantize(Decimal(str(tick_size)), rounding=ROUND_DOWN)
        else:
            limit_price = Decimal(current_price + tick_size).quantize(Decimal(str(tick_size)), rounding=ROUND_UP)
        if float(limit_price) < min_price:
            limit_price = Decimal(str(min_price))
        if float(limit_price) > max_price:
            limit_price = Decimal(str(max_price))
        print(f"Pripravujem LIMIT {side} za cenu {limit_price}, množstvo {qty}")
        try:
            print("Pred volaním client.create_order (LIMIT)")
            order = client.create_order(
                symbol=symbol,
                side=side,
                type='LIMIT',
                timeInForce='GTC',
                quantity=qty,
                price=str(limit_price)
            )
            print("Za volaním client.create_order (LIMIT)")
            print(f"LIMIT {side}: {order}")
            logging.info(f"LIMIT {side}: {order}")
            if 'status' in order:
                print(f"Stav LIMIT objednávky: {order['status']}")
                logging.info(f"Stav LIMIT objednávky: {order['status']}")
            return order
        except Exception as e:
            print(f"Chyba pri zadávaní LIMIT objednávky: {e}")
            import traceback
            traceback.print_exc()
            logging.error(f"Chyba pri zadávaní LIMIT objednávky: {e}")
            return None

def get_real_price(order, price_cache):
    return float(order.get('price', 0))

def close_loss_order(order_id):
    try:
        order = client.get_order(symbol=symbol, orderId=order_id)
        side = order['side']
        qty = float(order['origQty'])
        open_price = get_open_price(order)
        current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
        if (side == "BUY" and current_price < open_price) or (side == "SELL" and current_price > open_price):
            close_side = "SELL" if side == "BUY" else "BUY"
            qty_fmt = format_quantity(qty, STEP_SIZE)
            create_market_order(symbol, close_side, qty_fmt)
            print(f"Uzavretá stratová objednávka {order_id}")
        time.sleep(0.2)
    except Exception as e:
        print(f"Chyba pri uzatváraní objednávky {order_id}: {e}")

def close_all_loss_orders_parallel():
    stratove_objednavky = []
    with order_lock:
        for order_id in list(open_orders):
            try:
                order = client.get_order(symbol=symbol, orderId=order_id)
                side = order['side']
                qty = float(order['origQty'])
                open_price = get_open_price(order)  # použije správne aj fallback
                current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
                if (side == "BUY" and current_price < open_price) or (side == "SELL" and current_price > open_price):
                    stratove_objednavky.append(order_id)
            except Exception as e:
                print(f"Chyba pri kontrole objednávky {order_id}: {e}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        executor.map(close_loss_order, stratove_objednavky)

def sync_open_orders():
    with order_lock:
        orders = client.get_open_orders(symbol=symbol)
        open_orders.clear()
        order_open_time.clear()
        for order in orders:
            open_orders.append(order['orderId'])
            order_open_time[order['orderId']] = time.time()

def print_orders_status_detail():
    with order_lock:
        print("-" * 90)
        print("ID | Strana | Qty | Otvorené | Uzavreté | Cena | Aktuálna | Zmena | Stav | Hodnotenie")
        print("-" * 90)
        all_ids = set(open_orders) | set(profit_orders) | set(loss_orders)
        for order_id in all_ids:
            try:
                order = client.get_order(symbol=symbol, orderId=order_id)
                side = order['side']
                qty = float(order['origQty'])
                open_price = get_open_price(order)
                current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
                if open_price == 0:
                    price_change = 0
                else:
                    price_change = ((current_price - open_price) / open_price) * 100
                open_time = order_open_time.get(order_id, None)
                open_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(open_time)) if open_time else "-"
                close_time_str = "-"
                stav = "Čakajúca"
                color = "\033[93m"  # default žltá
                if order_id in profit_orders:
                    stav = "Zisková"
                    color = "\033[92m"
                    close_time = order_open_time.get(order_id, None)
                    close_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(close_time)) if close_time else "-"
                elif order_id in loss_orders:
                    stav = "Stratová"
                    color = "\033[91m"
                    close_time = order_open_time.get(order_id, None)
                    close_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(close_time)) if close_time else "-"
                print(
                    f"{color}ID:{order_id} | {side} | qty:{qty:.8f} | otvorené: {open_time_str} | uzavreté: {close_time_str} | "
                    f"cena:{open_price} | aktuálna:{current_price} | zmena:{price_change:.2f}% | stav:{order['status']} | hodnotenie:{stav}\033[0m"
            )
            except Exception as e:
                print(f"\033[91mChyba pri načítaní detailov objednávky {order_id}: {e}\033[0m")
        print("-" * 90)
        print(
            f"\033[93mČakajúcich: {len(open_orders)}\033[0m | "
            f"\033[92mZiskových: {len(profit_orders)}\033[0m | "
            f"\033[91mStratových: {len(loss_orders)}\033[0m"
        )
        print("-" * 90)

def print_orders_summary():
    with order_lock:
        waiting = len([oid for oid in open_orders if oid not in profit_orders and oid not in loss_orders])
        profit = len(profit_orders)
        loss = len(loss_orders)
        print("-" * 50)
        print(
            f"\033[93mČakajúcich: {waiting}\033[0m | "
            f"\033[92mZiskových:  {profit}\033[0m | "
            f"\033[91mStratových: {loss}\033[0m"
        )

        #
        # print(f"Čakajúcich: {waiting} | Ziskových: {profit} | Stratových: {loss}")
        print("-" * 50)

def monitor_market_close(symbol, open_price, qty, side, order_id):
    try:
        start_time = time.time()
        closed = False

        def get_lifetime():
            if MAX_ORDER_LIFETIME == 0:
                usdc_balance = float(client.get_asset_balance(asset='USDC')['free'])
                if usdc_balance > 100:
                    return 600
                elif usdc_balance > 50:
                    return 240
                else:
                    return 60
            else:
                return MAX_ORDER_LIFETIME

        profit_limit, loss_limit = get_limits_by_volatility()
        trailing_offset = get_dynamic_trailing_offset(abs(loss_limit))
        print(f"\033[96m[DYNAMICKÝ OFFSET] (start) Aktuálny trailing offset: {trailing_offset:.6f} (stratových: {len(loss_orders)})\033[0m")

        if side == "BUY":
            highest_price = open_price
            trailing_stop = open_price * (1 - trailing_offset)
            trailing_active = False
        else:
            lowest_price = open_price
            trailing_stop = open_price * (1 + trailing_offset)
            trailing_active = False

        last_offset = trailing_offset  # pridaj pred while not closed

        while not closed:
            elapsed = time.time() - start_time
            dynamic_lifetime = get_lifetime()
            current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
            if open_price == 0:
                profit = 0
            else:
                profit = (current_price - open_price) / open_price if side == "BUY" else (open_price - current_price) / open_price

            # --- DYNAMICKÝ OFFSET V CYKLE LEN PRI ZMENE ---
            trailing_offset = get_dynamic_trailing_offset(abs(loss_limit))
            if trailing_offset != last_offset:
                print(f"\033[94m[DYNAMICKÝ OFFSET] (cyklus) Aktuálny trailing offset: {trailing_offset:.6f} (stratových: {len(loss_orders)})\033[0m")
                last_offset = trailing_offset

            # Aktivuj trailing stop až po dosiahnutí profit_limit
            if side == "BUY":
                if not trailing_active and current_price >= open_price * (1 + profit_limit):
                    trailing_active = True
                    highest_price = current_price
                    trailing_stop = highest_price * (1 - trailing_offset)
                if trailing_active:
                    if current_price > highest_price:
                        highest_price = current_price
                        trailing_stop = highest_price * (1 - trailing_offset)
                    if current_price <= trailing_stop:
                        close_side = "SELL"
                        qty_fmt = format_quantity(qty, STEP_SIZE)
                        create_market_order(symbol, close_side, qty_fmt)
                        with order_lock:
                            if profit > 0:
                                if order_id not in profit_orders:
                                    profit_orders.append(order_id)
                            else:
                                if order_id not in loss_orders:
                                    loss_orders.append(order_id)
                            if order_id in open_orders:
                                open_orders.remove(order_id)
                        logging.info(f"BUY trailing stop aktivovaný pre {order_id} pri cene {current_price}, zisk {profit*100:.2f}%")
                        closed = True
                        continue

            else:  # SELL
                if not trailing_active and current_price <= open_price * (1 - profit_limit):
                    trailing_active = True
                    lowest_price = current_price
                    trailing_stop = lowest_price * (1 + trailing_offset)
                if trailing_active:
                    if current_price < lowest_price:
                        lowest_price = current_price
                        trailing_stop = lowest_price * (1 + trailing_offset)
                    if current_price >= trailing_stop:
                        close_side = "BUY"
                        qty_fmt = format_quantity(qty, STEP_SIZE)
                        create_market_order(symbol, close_side, qty_fmt)
                        with order_lock:
                            if profit > 0:
                                if order_id not in profit_orders:
                                    profit_orders.append(order_id)
                            else:
                                if order_id not in loss_orders:
                                    loss_orders.append(order_id)
                            if order_id in open_orders:
                                open_orders.remove(order_id)
                        logging.info(f"SELL trailing stop aktivovaný pre {order_id} pri cene {current_price}, zisk {profit*100:.2f}%")
                        closed = True
                        continue

            # Núdzové uzavretie po čase (ak treba)
            if elapsed > dynamic_lifetime:
                close_side = "SELL" if side == "BUY" else "BUY"
                qty_fmt = format_quantity(qty, STEP_SIZE)
                create_market_order(symbol, close_side, qty_fmt)
                with order_lock:
                    if profit > 0:
                        if order_id not in profit_orders:
                            profit_orders.append(order_id)
                    else:
                        if order_id not in loss_orders:
                            loss_orders.append(order_id)
                    if order_id in open_orders:
                        open_orders.remove(order_id)
                logging.info(f"Objednávka {order_id} uzavretá po {dynamic_lifetime} sekundách so ziskom {profit*100:.2f}%")
                closed = True

            time.sleep(2)
    except Exception as e:
        logging.error(f"Chyba v monitor_market_close pre order {order_id}: {e}")

def get_current_volatility():
    klines = client.get_klines(symbol=symbol, interval='1m', limit=10)
    closes = [float(k[4]) for k in klines]
    volatility = (max(closes) - min(closes)) / closes[0]
    return volatility

def get_limits_by_volatility():
    volatility = get_current_volatility()
    if volatility >= VOL_HIGH:
        profit_limit = PROFIT_LEVELS_HIGH[0]
        loss_limit = -LOSS_LIMIT_HIGH
    elif volatility >= VOL_MED:
        profit_limit = PROFIT_LEVELS_MED[0]
        loss_limit = -LOSS_LIMIT_MED
    else:
        profit_limit = PROFIT_LEVELS_LOW[0]
        loss_limit = -LOSS_LIMIT_LOW
    return profit_limit, loss_limit

def get_dynamic_trailing_offset(base_offset):
    loss_count = len(loss_orders)
    profit_count = len(profit_orders)

    # Najprv aplikuj multiplikátory pre straty (ako doteraz)
    for t, m in zip(reversed(loss_thresholds), reversed(loss_multipliers)):
        if loss_count >= t:
            base_offset = base_offset * m

    # Potom aplikuj multiplikátory pre zisky (čím viac ziskových, tým väčší offset)
    for t, m in zip(reversed(profit_thresholds), reversed(profit_multipliers)):
        if profit_count >= t:
            base_offset = base_offset * m

    return base_offset

def get_open_price(order):
    fills = order.get('fills', [])
    if fills and 'price' in fills[0]:
        return float(fills[0]['price'])
    try:
        executed_qty = float(order.get('executedQty', 0))
        cummulative_quote = float(order.get('cummulativeQuoteQty', 0))
        if executed_qty > 0 and cummulative_quote > 0:
            return cummulative_quote / executed_qty
        else:
            return float(order.get('price', 0))
    except Exception:
        return float(order.get('price', 0))

def update_dynamic_buy_sell_ratio():
    buy_profits = sum(1 for oid in profit_orders if client.get_order(symbol=symbol, orderId=oid)['side'] == "BUY")
    buy_losses = sum(1 for oid in loss_orders if client.get_order(symbol=symbol, orderId=oid)['side'] == "BUY")
    total_buys = buy_profits + buy_losses
    if total_buys < 5:
        return  # nechaj pôvodný pomer na začiatku
    ratio = buy_profits / total_buys if total_buys > 0 else 0.5
    global dynamic_buy_sell_ratio
    if ratio > 0.7:
        dynamic_buy_sell_ratio = 6
    elif ratio > 0.6:
        dynamic_buy_sell_ratio = 5
    elif ratio > 0.5:
        dynamic_buy_sell_ratio = 4
    elif ratio > 0.4:
        dynamic_buy_sell_ratio = 2
    else:
        dynamic_buy_sell_ratio = 1

def get_ema(symbol, interval='1m', period=20):
    klines = client.get_klines(symbol=symbol, interval=interval, limit=period+1)
    closes = np.array([float(k[4]) for k in klines])
    ema = closes[-period:].mean()  # jednoduchá EMA, môžeš nahradiť pokročilejšou
    return ema

# --- LOGOVANIE ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("trading.log", mode="a"),
        logging.StreamHandler()
    ]
)

# --- HLAVNÝ CYKLUS ---
logging.info("Bot bol spustený a logovanie funguje.")

while True:
    try:
        print(">>> Začiatok hlavného cyklu")
        logging.info(">>> Začiatok hlavného cyklu")

        # Sync open orders len každých 10 sekúnd
        if time.time() - last_sync > 120:
            sync_open_orders()
            last_sync = time.time()

        if bot_paused:
            print("\033[93mPauza aktívna: uzatváram všetky stratové pozície...\033[0m")
            logging.warning("Pauza aktívna: uzatváram všetky stratové pozície...")
            close_all_loss_orders_parallel()
            time.sleep(PAUSE_TIME)
            bot_paused = False
            print("\033[93mBot pokračuje v obchodovaní po pauze.\033[0m")
            logging.info("Bot pokračuje v obchodovaní po pauze.")

        now = time.time()
        last_price = float(client.get_symbol_ticker(symbol=symbol)['price'])

        with order_lock:
            if len(open_orders) >= MAX_OPEN_ORDERS:
                print("\033[91mMaximálny počet otvorených objednávok dosiahnutý! Nové objednávky sa neotvárajú.\033[0m")
                logging.warning("MAX OPEN ORDERS reached, skipping new order.")
                time.sleep(2)
                continue

        if now - last_trade_time > trade_interval:
            qty = TRADE_QTY
            qty_fmt = format_quantity(qty, STEP_SIZE)
            current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
            ema = get_ema(symbol, period=EMA_PERIOD)

            if trade_counter % (dynamic_buy_sell_ratio + 1) < dynamic_buy_sell_ratio:
                # BUY
                if current_price < ema:
                    logging.info(f"Trend filter: BUY zakázaný, cena pod EMA. Cena: {current_price}, EMA: {ema}")
                    continue
                usdc_balance = float(client.get_asset_balance(asset='USDC')['free'])
                if usdc_balance < 12:
                    print("\033[93mNedostatok USDC na BUY (zostatok: {:.2f}) – čakám...\033[0m".format(usdc_balance))
                    logging.warning(f"Nedostatok USDC na BUY (zostatok: {usdc_balance})")
                    time.sleep(10)
                    continue
                print(f"Volám create_order pre BUY, qty: {qty_fmt}")
                logging.info(f"Volám create_order pre BUY, qty: {qty_fmt}")
                order = create_order(symbol, "BUY", qty_fmt)
                if order:
                    logging.info(f"Objednávka {order['orderId']} ({'BUY'}) bola vytvorená: {order}")
                    open_price = get_open_price(order)
                    if open_price is None or open_price == 0:
                        open_price = last_price
                    with order_lock:
                        open_orders.append(order['orderId'])
                        order_open_time[order['orderId']] = time.time()
                    executor.submit(monitor_market_close, symbol, open_price, float(qty_fmt), "BUY", order['orderId'])
                    last_side = "BUY"
                    last_trade_time = now
            else:
                # SELL
                if current_price > ema:
                    print(f"Trend filter: SELL zakázaný, cena nad EMA. Cena: {current_price}, EMA: {ema}")
                    logging.info(f"Trend filter: SELL zakázaný, cena nad EMA. Cena: {current_price}, EMA: {ema}")
                    continue
                print(f"Volám create_order pre SELL, qty: {qty_fmt}")
                logging.info(f"Volám create_order pre SELL, qty: {qty_fmt}")
                order = create_order(symbol, "SELL", qty_fmt)
                if order:
                    logging.info(f"Objednávka {order['orderId']} ({'SELL'}) bola vytvorená: {order}")
                    open_price = get_open_price(order)
                    if open_price is None or open_price == 0:
                        open_price = last_price
                    with order_lock:
                        open_orders.append(order['orderId'])
                        order_open_time[order['orderId']] = time.time()
                    executor.submit(monitor_market_close, symbol, open_price, float(qty_fmt), "SELL", order['orderId'])
                    last_side = "SELL"
                    last_trade_time = now
            trade_counter += 1

         #Výpis reálnych objednávok len každých 120 sekúnd
        if time.time() - last_monitor > 120:
            print_orders_status_detail()
            last_monitor = time.time()

        update_dynamic_buy_sell_ratio()

        print_orders_summary()

        time.sleep(MAIN_LOOP_INTERVAL)
    except Exception as e:
        print(f"\033[91mChyba v hlavnom cykle: {e}\033[0m")
        logging.error(f"Chyba v hlavnom cykle: {e}")
        time.sleep(5)