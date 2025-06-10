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

def main():
    for bot, info in BOTS.items():
        log(f"--- Spracovanie {bot} ---")
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
            best_profit = get_best_profit(f"./{bot}_{info['log']}")
            log(f"Najlepší profit: {best_profit}")
        except Exception as e:
            log(f"[CHYBA] Vyhodnotenie profitu zlyhalo: {e}")
            continue
        # 3. Uprav konfiguráciu podľa výsledku
        new_profit_levels = "0.018,0.021,0.024,0.027"  # Tu môžeš použiť vlastnú logiku
        try:
            update_cfg(info['cfg'], new_profit_levels)
            log(f"Nová konfigurácia pre {bot} uložená.")
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
