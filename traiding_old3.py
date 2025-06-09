import logging
from binance.client import Client
import time
import threading
import concurrent.futures
import configparser

# Načítanie konfigurácie
config = configparser.ConfigParser()
config.read('trading.cfg')

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

api_key = config.get('API', 'API_KEY').strip('"')
api_secret = config.get('API', 'API_SECRET').strip('"')

client = Client(api_key, api_secret)
client.API_URL = 'https://testnet.binance.vision/api'
symbol = 'BTCUSDT'

order_lock = threading.Lock()

total_profit = 0.0
open_orders = []
profit_orders = []
loss_orders = []

logging.basicConfig(
    filename='trading_bot.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)

bot_paused = False

MAX_WORKERS = MAX_OPEN_ORDERS
executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)

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
    if vol > VOL_HIGH:
        profit_levels = DEFAULT_PROFIT_LEVELS_HIGH[:]
        loss_limit = DEFAULT_LOSS_LIMIT_HIGH
    elif vol > VOL_MED:
        profit_levels = DEFAULT_PROFIT_LEVELS_MED[:]
        loss_limit = DEFAULT_LOSS_LIMIT_MED
    else:
        profit_levels = DEFAULT_PROFIT_LEVELS_LOW[:]
        loss_limit = DEFAULT_LOSS_LIMIT_LOW

    # PRIORITA: Ak je veľa otvorených objednávok, zníž úrovne a skonči
    if len(open_orders) > 0.7 * MAX_OPEN_ORDERS:
        profit_levels = [x * 0.6 for x in profit_levels]
        loss_limit = loss_limit * 0.6
        logging.info("Dynamické zníženie profit/loss úrovní (veľa otvorených objednávok, trh stagnuje)")
        print(f"Aktuálna volatilita: {vol:.2f} | profit_levels: {profit_levels} | loss_limit: {loss_limit}")
        logging.info(f"Volatilita: {vol:.2f} | profit_levels: {profit_levels} | loss_limit: {loss_limit}")
        return profit_levels, loss_limit

    # Inak podľa výsledkov
    recent_trades = profit_orders[-10:] + loss_orders[-10:]
    recent_losses = [o for o in loss_orders[-10:]]
    if len(recent_trades) >= 6 and len(recent_losses) / len(recent_trades) > 0.5:
        profit_levels = [x * 0.7 for x in profit_levels]
        loss_limit = loss_limit * 0.7
        logging.info("Dynamické zníženie profit/loss úrovní (veľa strát v posledných obchodoch)")
    recent_profits = [o for o in profit_orders[-10:]]
    if len(recent_trades) >= 6 and len(recent_profits) / len(recent_trades) > 0.7:
        profit_levels = [x * 1.2 for x in profit_levels]
        loss_limit = loss_limit * 1.2
        logging.info("Dynamické zvýšenie profit/loss úrovní (veľa ziskov v posledných obchodoch)")

    print(f"Aktuálna volatilita: {vol:.2f} | profit_levels: {profit_levels} | loss_limit: {loss_limit}")
    logging.info(f"Volatilita: {vol:.2f} | profit_levels: {profit_levels} | loss_limit: {loss_limit}")
    return profit_levels, loss_limit

def monitor_market_close(symbol, open_price, qty, side="BUY"):
    global total_profit, profit_orders, loss_orders, open_orders
    closed = False
    closed_partials = set()
    order_id = None
    logging.info(f"Spúšťam monitorovanie objednávky: SIDE={side}, QTY={qty}, OPEN_PRICE={open_price}")
    while not closed:
        try:
            profit_levels, loss_limit = get_dynamic_thresholds(symbol)
            partials = {
                f"{profit_levels[0]:.2%}": 0.4 * qty,
                f"{profit_levels[1]:.2%}": 0.2 * qty,
                f"{profit_levels[2]:.2%}": 0.2 * qty,
                f"{profit_levels[3]:.2%}": 0.2 * qty
            }

            current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
            if side == "BUY":
                profit_percent = ((current_price - open_price) / open_price) * 100
                profit = (current_price - open_price) * qty
            else:
                profit_percent = ((open_price - current_price) / open_price) * 100
                profit = (open_price - current_price) * qty

            logging.info(f"Objednávka {side} | Aktuálny profit: {profit_percent:.4f}% | Cena: {current_price}")

            for i, level in enumerate(profit_levels):
                key = f"{level:.2%}"
                if key not in closed_partials and profit_percent >= level * 100:
                    close_side = "SELL" if side == "BUY" else "BUY"
                    order = create_market_order(symbol, close_side, partials[key])
                    if order:
                        close_price = get_real_price(order, {})
                        if close_price is None:
                            close_price = current_price
                        real_profit = (close_price - open_price) * partials[key] if side == "BUY" else (open_price - close_price) * partials[key]
                        with order_lock:
                            total_profit += real_profit
                            profit_orders.append({'orderId': order['orderId'], 'profit': real_profit, 'side': side})
                        logging.info(f"PROFIT PARTIAL: ID={order['orderId']} PROFIT={real_profit:.8f} SIDE={side} LEVEL={key}")
                        closed_partials.add(key)
                        order_id = order['orderId']

            if len(closed_partials) == 4:
                with order_lock:
                    for oid in list(open_orders):
                        if oid == order_id:
                            open_orders.remove(oid)
                logging.info(f"Všetky partialy uzavreté pre objednávku {order_id}")
                closed = True
                break

            if profit < 0:
                loss_percent = abs(profit_percent)
                logging.info(f"Kontrola straty: {loss_percent:.4f}% (limit: {loss_limit*100:.2f}%) pre order {qty}")
                if loss_percent >= loss_limit * 100:
                    close_side = "SELL" if side == "BUY" else "BUY"
                    remain_qty = sum(partials[k] for k in partials if k not in closed_partials)
                    if remain_qty > 0:
                        strat_order = create_market_order(symbol, close_side, remain_qty)
                        if strat_order:
                            close_price = get_real_price(strat_order, {})
                            if close_price is None:
                                close_price = current_price
                            real_profit = (close_price - open_price) * remain_qty if side == "BUY" else (open_price - close_price) * remain_qty
                            with order_lock:
                                total_profit += real_profit
                                loss_orders.append({'orderId': strat_order['orderId'], 'profit': real_profit, 'side': side})
                            logging.info(f"LOSS ORDER: ID={strat_order['orderId']} LOSS={real_profit:.8f} SIDE={side} LIMIT={loss_limit:.2f}")
                            for oid in list(open_orders):
                                if oid == strat_order['orderId']:
                                    open_orders.remove(oid)
                    logging.info(f"Stratová objednávka uzavretá pre order {qty}")
                    closed = True
                    break
                else:
                    logging.info(f"Stratová objednávka čaká na pokrytie strat (strata: {profit:.8f})")

            time.sleep(2)
        except Exception as e:
            logging.error(f"Chyba v monitor_market_close: {e}")
            time.sleep(2)

def monitor_orders():
    global bot_paused
    with order_lock:
        try:
            current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
        except Exception as e:
            print(f"Chyba pri získavaní aktuálnej ceny: {e}")
            logging.error(f"Chyba pri získavaní aktuálnej ceny: {e}")
            current_price = 0.0
        current_profit = 0.0
        active_profit = 0
        active_loss = 0
        for order_id in open_orders:
            try:
                order = client.get_order(symbol=symbol, orderId=order_id)
                if order['side'] == 'BUY' and order['status'] in ['FILLED', 'PARTIALLY_FILLED']:
                    buy_price = float(order['price'])
                    qty = float(order['origQty'])
                    if buy_price > 0:
                        profit = (current_price - buy_price) * qty
                        current_profit += profit
                        if profit >= 0:
                            active_profit += 1
                        else:
                            active_loss += 1
                elif order['side'] == 'SELL' and order['status'] in ['FILLED', 'PARTIALLY_FILLED']:
                    sell_price = float(order['price'])
                    qty = float(order['origQty'])
                    if sell_price > 0:
                        profit = (sell_price - current_price) * qty
                        current_profit += profit
                        if profit >= 0:
                            active_profit += 1
                        else:
                            active_loss += 1
            except Exception as e:
                print(f"Chyba pri výpočte aktuálneho profitu: {e}")
                logging.error(f"Chyba pri výpočte aktuálneho profitu: {e}")

        total_closed = len(profit_orders) + len(loss_orders)
        percent_profit = (len(profit_orders) / total_closed * 100) if total_closed > 0 else 0
        percent_loss = (len(loss_orders) / total_closed * 100) if total_closed > 0 else 0
        total_orders = len(open_orders) + total_closed
        percent_open = (len(open_orders) / total_orders * 100) if total_orders > 0 else 0
        total_profit_val = sum(o['profit'] for o in profit_orders) + sum(o['profit'] for o in loss_orders)

        print(f"\nCelkový profit od spustenia: {total_profit_val:.8f}")
        print(f"Aktuálny (momentálny) profit z otvorených objednávok: {current_profit:.8f}")
        print(f"\033[93mNeuzavreté ziskové objednávky: {active_profit}\033[0m")
        print(f"\033[93mNeuzavreté stratové objednávky: {active_loss}\033[0m")
        print("--- STAV OBJEDNÁVOK (od spustenia) ---")
        print(f"\033[94mOtvorené objednávky: {len(open_orders)} ({percent_open:.2f}%)\033[0m")
        print(f"\033[92mZiskové objednávky: {len(profit_orders)} ({percent_profit:.2f}%)\033[0m")
        print(f"\033[91mStratové objednávky: {len(loss_orders)} ({percent_loss:.2f}%)\033[0m")
        for o in profit_orders:
            print(f"\033[92mID: {o['orderId']} | Zisk: {o['profit']:.8f} | Smer: {o['side']}\033[0m")
            logging.info(f"PROFIT ORDER: ID={o['orderId']} PROFIT={o['profit']:.8f} SIDE={o['side']}")
        for o in loss_orders:
            print(f"\033[91mID: {o['orderId']} | Strata: {o['profit']:.8f} | Smer: {o['side']}\033[0m")
            logging.info(f"LOSS ORDER: ID={o['orderId']} LOSS={o['profit']:.8f} SIDE={o['side']}")
        for o in open_orders:
            print(f"\033[94mID: {o} | Otvorená objednávka\033[0m")

        # Automatická pauza pri extrémnych stratách
        if len(loss_orders) > 0 and len(profit_orders) > 0:
            if len(loss_orders) / max(1, len(profit_orders)) >= PAUSE_ON_LOSS_RATIO:
                print("\033[91mPríliš veľa stratových objednávok! Pauza bota na 60 sekúnd.\033[0m")
                logging.warning("PAUSE: Too many loss orders, bot is paused for 60 seconds.")
                bot_paused = True

last_monitor = time.time()
last_trade_time = 0
trade_interval = TRADE_INTERVAL
last_side = "SELL"

while True:
    try:
        if bot_paused:
            time.sleep(PAUSE_TIME)
            bot_paused = False
            print("\033[93mBot pokračuje v obchodovaní po pauze.\033[0m")
            logging.info("Bot resumed after pause.")

        now = time.time()
        trades = client.get_recent_trades(symbol=symbol)
        if trades:
            last_trade = trades[-1]
            last_price = float(last_trade['price'])
            print(f"Posledná cena: {last_price}")

            with order_lock:
                if len(open_orders) >= MAX_OPEN_ORDERS:
                    print("\033[91mMaximálny počet otvorených objednávok dosiahnutý! Nové objednávky sa neotvárajú.\033[0m")
                    logging.warning("MAX OPEN ORDERS reached, skipping new order.")
                    time.sleep(2)
                    continue

            if now - last_trade_time > trade_interval:
                qty = 0.001
                if last_side == "SELL":
                    print("Automaticky zadávam MARKET BUY (long).")
                    order = create_market_order(symbol, "BUY", qty)
                    if order:
                        price_cache = {}
                        open_price = get_real_price(order, price_cache)
                        if open_price is None:
                            open_price = last_price
                        with order_lock:
                            open_orders.append(order['orderId'])
                        executor.submit(monitor_market_close, symbol, open_price, qty, "BUY")
                        last_side = "BUY"
                        last_trade_time = now
                else:
                    print("Automaticky zadávam MARKET SELL (short).")
                    order = create_market_order(symbol, "SELL", qty)
                    if order:
                        price_cache = {}
                        open_price = get_real_price(order, price_cache)
                        if open_price is None:
                            open_price = last_price
                        with order_lock:
                            open_orders.append(order['orderId'])
                        executor.submit(monitor_market_close, symbol, open_price, qty, "SELL")
                        last_side = "SELL"
                        last_trade_time = now

        # Každých 15 sekúnd vypíš monitoring
        if time.time() - last_monitor > 15:
            monitor_orders()
            last_monitor = time.time()

        time.sleep(2)
    except Exception as e:
        print(f"\033[91mChyba v hlavnom cykle: {e}\033[0m")
        logging.error(f"Chyba v hlavnom cykle: {e}")
        time.sleep(5)