import os
import csv
import configparser
import subprocess
from datetime import datetime, timedelta
import sys
import shutil

# ===================== KONFIGURÁCIA PRAVIDIEL =====================
# Pravidlá optimalizácie pre každý bot (možno rozšíriť o ďalšie parametre)
OPT_RULES = {
    'PROFIT_LEVELS_HIGH': {
        'profit_threshold': 0,      # Akýkoľvek rast portfólia
        'increase_pct': 10,        # O koľko percent zvýšiť pri raste
        'decrease_pct': 10,        # O koľko percent znížiť pri poklese
        'min_value': 0.001,        # Minimálna povolená hodnota
        'max_value': 1.0           # Maximálna povolená hodnota
    },
    'PROFIT_LEVELS_MED': {
        'profit_threshold': 0,
        'increase_pct': 10,
        'decrease_pct': 10,
        'min_value': 0.001,
        'max_value': 1.0
    },
    'PROFIT_LEVELS_LOW': {
        'profit_threshold': 0,
        'increase_pct': 10,
        'decrease_pct': 10,
        'min_value': 0.001,
        'max_value': 1.0
    },
    'LOSS_LIMIT_HIGH': {
        'profit_threshold': 0,
        'increase_pct': 5,
        'decrease_pct': 5,
        'min_value': 0.0001,
        'max_value': 0.1
    },
    'LOSS_LIMIT_MED': {
        'profit_threshold': 0,
        'increase_pct': 5,
        'decrease_pct': 5,
        'min_value': 0.0001,
        'max_value': 0.1
    },
    'LOSS_LIMIT_LOW': {
        'profit_threshold': 0,
        'increase_pct': 5,
        'decrease_pct': 5,
        'min_value': 0.0001,
        'max_value': 0.1
    },
    'EMA_PERIOD': {
        'profit_threshold': 0,
        'increase_step': 1,
        'decrease_step': 1,
        'min_value': 5,
        'max_value': 100
    }
}

# ===================== NASTAVENIE BOTOV =====================
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

# ===================== LOGOVANIE =====================
def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")
    sys.stdout.flush()

def log_change(bot, param, old_value, new_value, csv_file, reason):
    with open("optimizer_changes.log", "a") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] bot: {bot} | param: {param} | pôvodné: {old_value} | nové: {new_value} | zdroj logu: {csv_file} | dôvod: {reason}\n")

def log_run(dry_run, bots):
    with open("optimizer_run.log", "a") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] dry_run: {dry_run} | bots: {', '.join(bots)}\n")

# ===================== NÁSTROJE =====================
def is_service_active(host, user, service):
    res = subprocess.run([
        "ssh",
        f"{user}@{host}",
        f"systemctl is-active {service}"
    ], capture_output=True, text=True)
    return res.returncode == 0 and res.stdout.strip() == "active"

def backup_cfg(cfg_file):
    backup_file = cfg_file + ".bak"
    shutil.copy2(cfg_file, backup_file)
    return backup_file

def restore_cfg(cfg_file, backup_file):
    shutil.copy2(backup_file, cfg_file)

# ===================== OPTIMALIZAČNÁ LOGIKA =====================
def get_profit_change_last_30min(csv_file):
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

def optimize_param(param, old_value, profit_change, rules):
    # Rozlíš, či je to zoznam (oddelený čiarkou) alebo jedno číslo
    if param.startswith('PROFIT_LEVELS'):
        levels = [float(x) for x in old_value.split(',')]
        if profit_change > rules['profit_threshold']:
            new_levels = [min(round(x * (1 + rules['increase_pct']/100), 5), rules['max_value']) for x in levels]
            reason = f"profit +{profit_change:.2f}, zvýšenie o {rules['increase_pct']}%"
        elif profit_change < -rules['profit_threshold']:
            new_levels = [max(round(x * (1 - rules['decrease_pct']/100), 5), rules['min_value']) for x in levels]
            reason = f"profit {profit_change:.2f}, zníženie o {rules['decrease_pct']}%"
        else:
            new_levels = levels
            reason = f"profit {profit_change:.2f}, bez zmeny"
        return ','.join(str(x) for x in new_levels), reason
    elif param.startswith('LOSS_LIMIT'):
        val = float(old_value)
        if profit_change > rules['profit_threshold']:
            new_val = min(round(val * (1 + rules['increase_pct']/100), 5), rules['max_value'])
            reason = f"profit +{profit_change:.2f}, zvýšenie o {rules['increase_pct']}%"
        elif profit_change < -rules['profit_threshold']:
            new_val = max(round(val * (1 - rules['decrease_pct']/100), 5), rules['min_value'])
            reason = f"profit {profit_change:.2f}, zníženie o {rules['decrease_pct']}%"
        else:
            new_val = val
            reason = f"profit {profit_change:.2f}, bez zmeny"
        return str(new_val), reason
    elif param == 'EMA_PERIOD':
        val = int(float(old_value))
        if profit_change > rules['profit_threshold']:
            new_val = min(val + rules['increase_step'], rules['max_value'])
            reason = f"profit +{profit_change:.2f}, zvýšenie o {rules['increase_step']}"
        elif profit_change < -rules['profit_threshold']:
            new_val = max(val - rules['decrease_step'], rules['min_value'])
            reason = f"profit {profit_change:.2f}, zníženie o {rules['decrease_step']}"
        else:
            new_val = val
            reason = f"profit {profit_change:.2f}, bez zmeny"
        return str(new_val), reason
    else:
        # fallback: nemen parametre, ktoré nepoznáme
        return old_value, "neoptimalizované"

# ===================== DEPLOYMENT =====================
def deploy_cfg(bot, info, dry_run=False):
    # 1. Stiahni log z bota
    log(f"Sťahujem log z {bot}...")
    res = subprocess.run([
        "scp",
        f"{info['user']}@{info['host']}:{info['remote_path']}{info['log']}",
        f"./{bot}_{info['log']}"
    ], capture_output=True, text=True)
    if res.returncode != 0:
        log(f"[CHYBA] SCP logu zlyhalo: {res.stderr.strip()}")
        return False
    log(f"Log úspešne stiahnutý.")
    # 2. Vyhodnoť zmenu portfólia
    profit_change = get_profit_change_last_30min(f"./{bot}_{info['log']}")
    log(f"Zmena portfólia za posledných 30 minút: {profit_change}")
    # 3. Optimalizuj parametre podľa pravidiel
    config = configparser.ConfigParser()
    config.read(info['cfg'])
    changed = False
    for param, rules in OPT_RULES.items():
        old_value = config['BOT'].get(param, None)
        if old_value is None:
            continue
        new_value, reason = optimize_param(param, old_value, profit_change, rules)
        if new_value != old_value:
            changed = True
            log_change(bot, param, old_value, new_value, f"./{bot}_{info['log']}", reason)
            log(f"Optimalizované {param}: {old_value} -> {new_value} ({reason})")
            if not dry_run:
                config['BOT'][param] = new_value
    if not changed:
        log(f"Žiadna zmena parametrov pre {bot}.")
        return True
    # 4. Zálohuj pôvodný .cfg
    if not dry_run:
        backup_file = backup_cfg(info['cfg'])
        log(f"Záloha konfigurácie: {backup_file}")
        # 5. Ulož novú konfiguráciu
        with open(info['cfg'], 'w') as f:
            config.write(f)
        log(f"Nová konfigurácia pre {bot} uložená.")
        # 6. Upload na bota
        res = subprocess.run([
            "scp",
            info['cfg'],
            f"{info['user']}@{info['host']}:{info['remote_path']}{info['cfg']}"
        ], capture_output=True, text=True)
        if res.returncode != 0:
            log(f"[CHYBA] SCP konfigurácie zlyhalo: {res.stderr.strip()}")
            restore_cfg(info['cfg'], backup_file)
            log(f"Obnovená pôvodná konfigurácia zo zálohy.")
            return False
        log(f"Konfigurácia úspešne uploadovaná.")
        # 7. Reštartuj bota
        res = subprocess.run([
            "ssh",
            f"{info['user']}@{info['host']}",
            "sudo systemctl restart trading-bot"
        ], capture_output=True, text=True)
        if res.returncode == 0:
            log(f"{bot} reštartovaný s novou konfiguráciou.")
        else:
            log(f"[CHYBA] Reštart bota zlyhal: {res.stderr.strip()}")
    return True

# ===================== HLAVNÝ CYKLUS =====================
def main(dry_run=False):
    log_run(dry_run, list(BOTS.keys()))
    for bot, info in BOTS.items():
        log(f"--- Spracovanie {bot} ---")
        trading_active = is_service_active(info['host'], info['user'], "trading-bot")
        wallet_active = is_service_active(info['host'], info['user'], "wallet-status")
        if not trading_active or not wallet_active:
            log(f"[VAROVANIE] Služby trading-bot alebo wallet-status nie sú aktívne na {bot}. Preskakujem optimalizáciu.")
            log(f"Stav trading-bot: {'aktívna' if trading_active else 'neaktívna'}, wallet-status: {'aktívna' if wallet_active else 'neaktívna'}")
            log(f"--- Hotovo pre {bot} ---\n")
            continue
        try:
            deploy_cfg(bot, info, dry_run=dry_run)
        except Exception as e:
            log(f"[CHYBA] Optimalizácia/deployment zlyhal: {e}")
        log(f"--- Hotovo pre {bot} ---\n")

if __name__ == "__main__":
    dry_run = '--dry-run' in sys.argv
    main(dry_run=dry_run)
