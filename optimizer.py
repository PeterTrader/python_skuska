import os
import csv
import configparser
import subprocess
from datetime import datetime
import sys

# Nastavenie ciest a botov (prispôsob podľa reálneho nasadenia)
BOTS = {
    "bot1": {
        "host": "34.146.103.137",
        "user": "traderpeter47",
        "cfg": "ema10_limit.cfg",
        "log": "wallet_log_ema10_limit.cfg.csv",
        "remote_path": "/home/traderpeter47/python_skuska/"
    },
    # Pridaj ďalšie boty podľa potreby
}

# Príklad: vyhodnotí najvyšší total_usdc z logu
def get_best_profit(csv_file):
    best_profit = -float('inf')
    with open(csv_file, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                profit = float(row['total_usdc'])
                if profit > best_profit:
                    best_profit = profit
            except Exception:
                continue
    return best_profit

def update_cfg(cfg_file, new_profit_levels):
    config = configparser.ConfigParser()
    config.read(cfg_file)
    config['BOT']['PROFIT_LEVELS_HIGH'] = new_profit_levels
    with open(cfg_file, 'w') as f:
        config.write(f)

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")
    sys.stdout.flush()

def is_service_active(host, user, service):
    res = subprocess.run([
        "ssh",
        f"{user}@{host}",
        f"systemctl is-active {service}"
    ], capture_output=True, text=True)
    return res.returncode == 0 and res.stdout.strip() == "active"

def log_change(bot, old_value, new_value, csv_file):
    with open("optimizer_changes.log", "a") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] bot: {bot} | pôvodné PROFIT_LEVELS_HIGH: {old_value} | nové PROFIT_LEVELS_HIGH: {new_value} | zdroj logu: {csv_file}\n")

def get_profit_change_last_30min(csv_file):
    # Zistí zmenu total_usdc za posledných 30 minút
    rows = []
    with open(csv_file, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row_time = datetime.strptime(row['timestamp'], '%Y-%m-%d %H:%M:%S')
                row['row_time'] = row_time
                rows.append(row)
            except Exception:
                continue
    if not rows:
        return 0.0
    rows.sort(key=lambda r: r['row_time'])
    now = rows[-1]['row_time']
    # Najdi najnovší záznam starý aspoň 30 minút
    old_row = None
    for row in reversed(rows):
        if (now - row['row_time']).total_seconds() >= 1800:
            old_row = row
            break
    if not old_row:
        old_row = rows[0]
    try:
        profit_change = float(rows[-1]['total_usdc']) - float(old_row['total_usdc'])
    except Exception:
        profit_change = 0.0
    return profit_change

def adjust_profit_levels(old_levels, profit_change):
    # Automaticky upraví PROFIT_LEVELS_HIGH podľa zmeny portfólia
    levels = [float(x) for x in old_levels.split(',')]
    if profit_change > 0:
        # Zvýš o 10 %
        new_levels = [round(x * 1.1, 5) for x in levels]
    elif profit_change < 0:
        # Zníž o 10 %
        new_levels = [round(x * 0.9, 5) for x in levels]
    else:
        new_levels = levels
    return ','.join(str(x) for x in new_levels)

def main():
    for bot, info in BOTS.items():
        log(f"--- Spracovanie {bot} ---")
        # Skontroluj, či sú služby aktívne
        trading_active = is_service_active(info['host'], info['user'], "trading-bot")
        wallet_active = is_service_active(info['host'], info['user'], "wallet-status")
        if not trading_active or not wallet_active:
            log(f"[VAROVANIE] Služby trading-bot alebo wallet-status nie sú aktívne na {bot}. Preskakujem optimalizáciu.")
            log(f"Stav trading-bot: {'aktívna' if trading_active else 'neaktívna'}, wallet-status: {'aktívna' if wallet_active else 'neaktívna'}")
            log(f"--- Hotovo pre {bot} ---\n")
            continue
        # 1. Stiahni log z bota
        log(f"Sťahujem log z {bot}...")
        res = subprocess.run([
            "scp",
            f"{info['user']}@{info['host']}:{info['remote_path']}{info['log']}",
            f"./{bot}_{info['log']}"
        ], capture_output=True, text=True)
        if res.returncode == 0:
            log(f"Log úspešne stiahnutý.")
        else:
            log(f"[CHYBA] SCP logu zlyhalo: {res.stderr.strip()}")
            continue
        # 2. Vyhodnoť výsledky
        try:
            profit_change = get_profit_change_last_30min(f"./{bot}_{info['log']}")
            log(f"Zmena portfólia za posledných 30 minút: {profit_change}")
        except Exception as e:
            log(f"[CHYBA] Vyhodnotenie zmeny portfólia zlyhalo: {e}")
            continue
        # 3. Uprav konfiguráciu podľa výsledku
        config = configparser.ConfigParser()
        config.read(info['cfg'])
        old_value = config['BOT'].get('PROFIT_LEVELS_HIGH', 'N/A')
        new_profit_levels = adjust_profit_levels(old_value, profit_change)
        try:
            update_cfg(info['cfg'], new_profit_levels)
            log(f"Nová konfigurácia pre {bot} uložená.")
            log_change(bot, old_value, new_profit_levels, f"./{bot}_{info['log']}")
        except Exception as e:
            log(f"[CHYBA] Ukladanie konfigurácie zlyhalo: {e}")
            continue
        # 4. Pošli novú konfiguráciu na bota
        log(f"Uploadujem novú konfiguráciu na {bot}...")
        res = subprocess.run([
            "scp",
            info['cfg'],
            f"{info['user']}@{info['host']}:{info['remote_path']}{info['cfg']}"
        ], capture_output=True, text=True)
        if res.returncode == 0:
            log(f"Konfigurácia úspešne uploadovaná.")
        else:
            log(f"[CHYBA] SCP konfigurácie zlyhalo: {res.stderr.strip()}")
            continue
        # 5. Reštartuj bota cez SSH cez systemctl
        log(f"Reštartujem bota na {bot}...")
        res = subprocess.run([
            "ssh",
            f"{info['user']}@{info['host']}",
            "sudo systemctl restart trading-bot"
        ], capture_output=True, text=True)
        if res.returncode == 0:
            log(f"{bot} reštartovaný s novou konfiguráciou.")
        else:
            log(f"[CHYBA] Reštart bota zlyhal: {res.stderr.strip()}")
        log(f"--- Hotovo pre {bot} ---\n")

if __name__ == "__main__":
    main()
