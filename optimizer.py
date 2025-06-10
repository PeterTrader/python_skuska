import os
import csv
import configparser
import subprocess
from datetime import datetime

# Nastavenie ciest a botov (prispôsob podľa reálneho nasadenia)
BOTS = {
    "bot1": {"host": "bot1_ip", "user": "user1", "cfg": "ema10_limit.cfg", "log": "wallet_log_ema10_limit.cfg.csv"},
    "bot2": {"host": "bot2_ip", "user": "user2", "cfg": "ema10_limit.cfg", "log": "wallet_log_ema10_limit.cfg.csv"},
    "bot3": {"host": "bot3_ip", "user": "user3", "cfg": "ema10_limit.cfg", "log": "wallet_log_ema10_limit.cfg.csv"},
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

def main():
    for bot, info in BOTS.items():
        # 1. Stiahni log z bota
        print(f"[INFO] Sťahujem log z {bot}...")
        subprocess.run([
            "scp",
            f"{info['user']}@{info['host']}:/cesta/na/bot/{info['log']}",
            f"./{bot}_{info['log']}"
        ])
        # 2. Vyhodnoť výsledky
        best_profit = get_best_profit(f"./{bot}_{info['log']}")
        print(f"{bot}: Najlepší profit: {best_profit}")
        # 3. Uprav konfiguráciu podľa výsledku (príklad: zvýš PROFIT_LEVELS_HIGH)
        new_profit_levels = "0.018,0.021,0.024,0.027"  # Tu môžeš použiť vlastnú logiku
        update_cfg(info['cfg'], new_profit_levels)
        print(f"[INFO] Nová konfigurácia pre {bot} uložená.")
        # 4. Pošli novú konfiguráciu na bota
        subprocess.run([
            "scp",
            info['cfg'],
            f"{info['user']}@{info['host']}:/cesta/na/bot/{info['cfg']}"
        ])
        # 5. Reštartuj bota cez SSH
        subprocess.run([
            "ssh",
            f"{info['user']}@{info['host']}",
            "pkill -f traiding.py && nohup python3 traiding.py ema10_limit.cfg &"
        ])
        print(f"[INFO] {bot} reštartovaný s novou konfiguráciou.")

if __name__ == "__main__":
    main()
