import time
from binance.client import Client
import configparser

# Nastav API kľúč hlavného účtu (master key s právami na sub-accounts)
API_KEY = "VLOZ_SVOJ_MASTER_API_KEY"
API_SECRET = "VLOZ_SVOJ_MASTER_API_SECRET"

client = Client(API_KEY, API_SECRET)

# Ako často kontrolovať subúčty (v sekundách)
CHECK_INTERVAL = 3600  # 60 minút (zvýšené kvôli API limitu)

# Zoznam subúčtov (emaily alebo iné identifikátory)
# Ak chceš dynamicky, môžeš použiť client.get_sub_account_list()
# Tu je ukážka s emailmi:
SUBACCOUNTS = []

# Získaj zoznam sub-accounts (prvýkrát)
try:
    subaccounts_info = client.get_sub_account_list()
    for sub in subaccounts_info['subAccounts']:
        SUBACCOUNTS.append(sub['email'])
except Exception as e:
    print(f"Chyba pri získavaní zoznamu subúčtov: {e}")

print(f"Monitorujem subúčty: {SUBACCOUNTS}")

while True:
    print(f"\n--- Kontrola účtov ---")
    # Hlavný účet (master)
    try:
        master_balances = client.get_account()['balances']
        usdc = next((float(b['free']) + float(b['locked']) for b in master_balances if b['asset'] == 'USDC'), 0.0)
        btc = next((float(b['free']) + float(b['locked']) for b in master_balances if b['asset'] == 'BTC'), 0.0)
        print(f"Hlavný účet | USDC: {usdc} | BTC: {btc}")
    except Exception as e:
        print(f"Chyba pri čítaní hlavného účtu: {e}")

    for email in SUBACCOUNTS:
        try:
            # Získaj zostatky na subúčte
            assets = client.get_sub_account_assets(email=email)
            balances = assets.get('balances', [])
            usdc = next((float(b['free']) + float(b['locked']) for b in balances if b['asset'] == 'USDC'), 0.0)
            btc = next((float(b['free']) + float(b['locked']) for b in balances if b['asset'] == 'BTC'), 0.0)

            # --- Pridanie ďalších metrík ---
            # Príklad: Zisk (profit), počet obchodov, PnL
            profit = None
            num_trades = None
            pnl = None
            try:
                # Futures profit a PnL (ak je povolené a dostupné)
                futures = client.get_sub_account_futures_account(email=email)
                profit = float(futures.get('totalUnrealizedProfit', 0.0))
                pnl = float(futures.get('totalMarginBalance', 0.0))
                # Počet obchodov nie je priamo dostupný, treba volať históriu obchodov alebo logy
                # num_trades = ...
            except Exception as e:
                # Ak futures nie je povolené alebo API nedostupné, nechaj None
                pass

            print(f"Subúčet: {email} | USDC: {usdc} | BTC: {btc} | Profit: {profit} | PnL: {pnl} | Počet obchodov: {num_trades}")
            # Ak chceš získať počet obchodov, môžeš doplniť logiku podľa svojich logov alebo ďalších API volaní
        except Exception as e:
            print(f"Chyba pri čítaní subúčtu {email}: {e}")
    print(f"Čakám {CHECK_INTERVAL//60} minút na ďalšiu kontrolu...")
    time.sleep(CHECK_INTERVAL)
