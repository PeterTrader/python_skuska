import configparser
from binance.client import Client
import time
import os

# Načítanie API kľúčov z api.cfg
api_config = configparser.ConfigParser()
api_config.read('api.cfg')
api_key = api_config.get('API', 'API_KEY').strip('"')
api_secret = api_config.get('API', 'API_SECRET').strip('"')

client = Client(api_key, api_secret)
symbol = 'BTCUSDC'  # Alebo načítaj zo svojho configu

def is_trading_bot_running():
    try:
        output = os.popen("ps aux | grep traiding.py | grep -v grep").read()
        return "traiding.py" in output
    except Exception:
        return False

def monitor_trades():
    trades = client.get_my_trades(symbol=symbol)
    pairs = []
    last_buy = None
    profit_count = 0
    loss_count = 0
    profit_sum = 0
    loss_sum = 0

    for trade in trades:
        side = 'BUY' if trade['isBuyer'] else 'SELL'
        qty = float(trade['qty'])
        price = float(trade['price'])
        if side == 'BUY':
            last_buy = (qty, price)
        elif side == 'SELL' and last_buy:
            buy_qty, buy_price = last_buy
            trade_profit = (price - buy_price) * min(qty, buy_qty)
            pairs.append((buy_price, price, trade_profit))
            if trade_profit > 0:
                profit_count += 1
                profit_sum += trade_profit
            else:
                loss_count += 1
                loss_sum += trade_profit
            last_buy = None

    total_trades = profit_count + loss_count
    percent_profit = (profit_count / total_trades) * 100 if total_trades > 0 else 0
    percent_loss = (loss_count / total_trades) * 100 if total_trades > 0 else 0
    net_result = profit_sum + loss_sum

    print("\n--- HISTÓRIA OBCHODOV ---")
    print(f"\033[92mZiskové obchody: {profit_count} ({percent_profit:.2f}%) | Celkový zisk: {profit_sum:.8f}\033[0m")
    print(f"\033[91mStratové obchody: {loss_count} ({percent_loss:.2f}%) | Celková strata: {loss_sum:.8f}\033[0m")
    print(f"\033[94mCelkovo {'ZISK' if net_result > 0 else 'STRATA'}: {net_result:.8f}\033[0m")
    for i, (buy, sell, profit) in enumerate(pairs):
        color = "\033[92m" if profit > 0 else "\033[91m"
        print(f"{color}Pár {i+1}: BUY {buy} → SELL {sell} | Profit: {profit:.8f}\033[0m")

if __name__ == "__main__":
    while True:
        bot_on = is_trading_bot_running()
        if bot_on:
            print("\033[92mSTAV: ON\033[0m")
        else:
            print("\033[91mSTAV: OFF\033[0m")
        monitor_trades()
        time.sleep(60)