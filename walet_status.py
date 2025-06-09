from binance.client import Client
import time
import configparser
import sys
import psutil
from datetime import datetime
import csv
import os

api_config = configparser.ConfigParser()
api_config.read('api.cfg')

api_key = api_config.get('API', 'API_KEY').strip('"')
api_secret = api_config.get('API', 'API_SECRET').strip('"')

client = Client(api_key, api_secret)

def get_balance(asset_name="USDT"):
    balances = client.get_account()['balances']
    for asset in balances:
        if asset['asset'] == asset_name:
            return float(asset['free']) + float(asset['locked'])
    return 0.0

def close_all_open_orders(symbol):
    open_orders = client.get_open_orders(symbol=symbol)
    cancelled = 0
    if open_orders:
        print(f"\033[93mOtvorené objednávky pred panic uzatvorením:\033[0m")
        for order in open_orders:
            print(f"\033[93mID: {order['orderId']} | {order['side']} | {order['origQty']} @ {order['price']}\033[0m")
    for order in open_orders:
        try:
            client.cancel_order(symbol=symbol, orderId=order['orderId'])
            print(f"\033[91mZrušená objednávka: {order['orderId']}\033[0m")
            cancelled += 1
        except Exception as e:
            print(f"\033[91mChyba pri rušení objednávky {order['orderId']}: {e}\033[0m")
    print(f"\033[91mVšetky otvorené objednávky boli uzavreté (PANIC MODE). Počet zrušených: {cancelled}\033[0m")

def is_trading_bot_running():
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['cmdline'] and 'traiding.py' in ' '.join(proc.info['cmdline']):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False

def log_to_csv(filename, data):
    file_exists = os.path.isfile(filename)
    with open(filename, mode='a', newline='') as csvfile:
        writer = csv.writer(csvfile)
        if not file_exists:
            writer.writerow(['datetime', 'config', 'status', 'usdc_balance', 'btc_balance', 'btc_price', 'total_usdc', 'note'])
        writer.writerow(data)

if __name__ == "__main__":
    symbol = "BTCUSDC"
    asset = "USDC"
    last_wallet_check = 0
    wallet_check_interval = 30  # sekúnd

    prev_usdc = None
    prev_btc = None

    config_name = sys.argv[1] if len(sys.argv) > 1 else "default"
    csv_log = f"wallet_log_{config_name}.csv"

    while True:
        status = "ON"
        note = ""
        if not is_trading_bot_running():
            status = "OFF"
            note = "PANIC: Bot nebeží, uzatváram všetky objednávky"
            print("\033[91mSTAV: OFF - PANIC! Uzatváram všetky objednávky...\033[0m")
            close_all_open_orders(symbol)
            print("\033[91mVšetky otvorené objednávky boli uzavreté (PANIC MODE)\033[0m")
            time.sleep(10)
            continue
        else:
            print("\033[92mSTAV: ON\033[0m")

        now = time.time()
        if now - last_wallet_check > wallet_check_interval:
            usdc_balance = get_balance("USDC")
            btc_balance = get_balance("BTC")
            btc_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
            total_usdc = usdc_balance + btc_balance * btc_price

            usdc_diff = ""
            btc_diff = ""
            if prev_usdc is not None:
                usdc_change = usdc_balance - prev_usdc
                if abs(usdc_change) > 0.00001:
                    if usdc_change > 0:
                        usdc_diff = f" (\033[92m+{usdc_change:.6f}\033[0m)"
                    else:
                        usdc_diff = f" (\033[91m{usdc_change:.6f}\033[0m)"
            if prev_btc is not None:
                btc_change = btc_balance - prev_btc
                if abs(btc_change) > 0.00000001:
                    if btc_change > 0:
                        btc_diff = f" (\033[92m+{btc_change:.8f}\033[0m)"
                    else:
                        btc_diff = f" (\033[91m{btc_change:.8f}\033[0m)"

            print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | USDC zostatok: {usdc_balance}{usdc_diff} | BTC zostatok: {btc_balance}{btc_diff} | Celková hodnota: {total_usdc:.2f} USDC")

            # Logovanie do CSV
            log_to_csv(csv_log, [
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                config_name,
                status,
                usdc_balance,
                btc_balance,
                btc_price,
                total_usdc,
                note
            ])

            prev_usdc = usdc_balance
            prev_btc = btc_balance
            last_wallet_check = now

        time.sleep(1)