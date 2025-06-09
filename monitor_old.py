from binance.client import Client
import time
import os

api_key = "5nVyBj70NIA0xgyld6wd0d2g68EVPoURtvsSFNwtobjbXGTHifUsalcijg8x66en"
api_secret = "zpuDNdkXIiYhFSR6o7O5ERVdGXRD8oyA5AcTdVjy5urvSjELrUwQnpQg0HUZLTV4"

client = Client(api_key, api_secret)
client.API_URL = 'https://testnet.binance.vision/api'

symbol = 'BTCUSDT'

def get_open_orders(symbol):
    return client.get_open_orders(symbol=symbol)

def get_all_orders(symbol):
    return client.get_all_orders(symbol=symbol)

def close_all_open_orders(symbol):
    open_orders = get_open_orders(symbol)
    for order in open_orders:
        try:
            client.cancel_order(symbol=symbol, orderId=order['orderId'])
            print(f"\033[91mZrušená objednávka: {order['orderId']}\033[0m")
        except Exception as e:
            print(f"\033[91mChyba pri rušení objednávky {order['orderId']}: {e}\033[0m")

def is_trading_bot_running():
    # Skontroluje, či beží proces traiding.py
    try:
        output = os.popen("ps aux | grep traiding.py | grep -v grep").read()
        return "traiding.py" in output
    except Exception:
        return False

def monitor_orders():
    all_orders = get_all_orders(symbol)
    open_orders = []
    profit_orders = []
    loss_orders = []

    for order in all_orders:
        if order['status'] in ['NEW', 'PARTIALLY_FILLED']:
            open_orders.append(order)
        elif order['status'] == 'FILLED':
            side = order['side']
            price = float(order['price']) if float(order['price']) > 0 else None
            qty = float(order['origQty'])
            if side == 'BUY' and price:
                current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
                profit = (current_price - price) * qty
                if profit > 0:
                    profit_orders.append((order, profit))
                else:
                    loss_orders.append((order, profit))

    print("\n--- STAV OBJEDNÁVOK ---")
    print(f"\033[94mOtvorené objednávky: {len(open_orders)}\033[0m")
    print(f"\033[92mZiskové objednávky: {len(profit_orders)}\033[0m")
    print(f"\033[91mStratové objednávky: {len(loss_orders)}\033[0m")

    for order, profit in profit_orders:
        print(f"\033[92mID: {order['orderId']} | Zisk: {profit:.8f}\033[0m")
    for order, profit in loss_orders:
        print(f"\033[91mID: {order['orderId']} | Strata: {profit:.8f}\033[0m")
    for order in open_orders:
        print(f"\033[94mID: {order['orderId']} | Otvorená objednávka\033[0m")

if __name__ == "__main__":
    while True:
        bot_on = is_trading_bot_running()
        if bot_on:
            print("\033[92mSTAV: ON\033[0m")  # zelený stav
            monitor_orders()
        else:
            print("\033[91mSTAV: OFF - PANIC! Uzatváram všetky objednávky...\033[0m")  # červený stav
            close_all_open_orders(symbol)
            print("\033[91mVšetky otvorené objednávky boli uzavreté (PANIC MODE)\033[0m")
            break
        time.sleep(30)