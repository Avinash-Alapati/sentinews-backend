import httpx
import csv
import io
import json

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
client = httpx.Client(headers=headers, timeout=20.0)

csv_urls = {
    'nifty50': 'https://archives.nseindia.com/content/indices/ind_nifty50list.csv',
    'nifty100': 'https://archives.nseindia.com/content/indices/ind_nifty100list.csv',
    'nifty500': 'https://archives.nseindia.com/content/indices/ind_nifty500list.csv',
    'midcap100': 'https://archives.nseindia.com/content/indices/ind_niftymidcap100list.csv',
    'smallcap100': 'https://archives.nseindia.com/content/indices/ind_niftysmallcap100list.csv',
    'total_market': 'https://archives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv',
}

symbols_by_index = {}
names_map = {}

for name, u in csv_urls.items():
    r = client.get(u)
    reader = csv.DictReader(io.StringIO(r.text))
    syms = []
    for row in reader:
        sym = row.get('Symbol', '').strip().upper()
        cname = row.get('Company Name', '').strip()
        if sym:
            syms.append(sym)
            if cname and sym not in names_map:
                names_map[sym] = cname
    symbols_by_index[name] = syms

# Add aliases
if 'TMPV' not in names_map:
    names_map['TMPV'] = 'Tata Motors Passenger Vehicles Ltd'
if 'TMCV' not in names_map:
    names_map['TMCV'] = 'Tata Motors Commercial Vehicles Ltd'
if 'ETERNAL' not in names_map:
    names_map['ETERNAL'] = 'Eternal Ltd (formerly Zomato)'
if 'TATAMOTORS' not in names_map:
    names_map['TATAMOTORS'] = 'Tata Motors Passenger Vehicles Ltd'
if 'ZOMATO' not in names_map:
    names_map['ZOMATO'] = 'Eternal Ltd (formerly Zomato)'

# Write to app/integrations/market/index_constituents.py
content = f'''\"\"\"
Official constituent sets for Indian equity indices:
- NIFTY 50
- NIFTY 100
- NIFTY 500
- NIFTY MIDCAP 100
- NIFTY SMALLCAP 100
- NIFTY TOTAL MARKET
\"\"\"
from typing import Set, Dict, Optional

NIFTY_50_SYMBOLS: Set[str] = {repr(set(symbols_by_index['nifty50']))}
NIFTY_100_SYMBOLS: Set[str] = {repr(set(symbols_by_index['nifty100']))}
NIFTY_500_SYMBOLS: Set[str] = {repr(set(symbols_by_index['nifty500']))}
NIFTY_MIDCAP_100_SYMBOLS: Set[str] = {repr(set(symbols_by_index['midcap100']))}
NIFTY_SMALLCAP_100_SYMBOLS: Set[str] = {repr(set(symbols_by_index['smallcap100']))}
NIFTY_TOTAL_MARKET_SYMBOLS: Set[str] = {repr(set(symbols_by_index['total_market']))}

COMPANY_NAMES_MAP: Dict[str, str] = {json.dumps(names_map, indent=4)}

def matches_index_filter(symbol: str, index_filter: Optional[str]) -> bool:
    if not index_filter or index_filter.lower() in ("all", "total_market", "totalmarket", "nifty_total_market", "nifty total market", "indian_market"):
        return True
    
    clean = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
    # Handle aliases
    if clean in ("TATAMOTORS", "TATA_MOTORS"):
        clean = "TMPV"
    elif clean == "ZOMATO":
        clean = "ETERNAL"
        
    f = index_filter.lower().replace(" ", "").replace("_", "").replace("-", "")
    if f in ("nifty50", "50"):
        return clean in NIFTY_50_SYMBOLS
    elif f in ("nifty100", "100"):
        return clean in NIFTY_100_SYMBOLS
    elif f in ("nifty500", "500"):
        return clean in NIFTY_500_SYMBOLS
    elif f in ("midcap100", "niftymidcap100", "midcap"):
        return clean in NIFTY_MIDCAP_100_SYMBOLS
    elif f in ("smallcap100", "niftysmallcap100", "smallcap"):
        return clean in NIFTY_SMALLCAP_100_SYMBOLS
    elif f in ("totalmarket", "niftytotalmarket"):
        return clean in NIFTY_TOTAL_MARKET_SYMBOLS
        
    return True

def get_constituent_company_name(symbol: str) -> Optional[str]:
    clean = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
    return COMPANY_NAMES_MAP.get(clean)
'''

with open('app/integrations/market/index_constituents.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Successfully written app/integrations/market/index_constituents.py with NIFTY 100 included!')
