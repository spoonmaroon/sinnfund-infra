#!/usr/bin/env python3
"""Build a local broad-sector map from the official Nasdaq screener."""

import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
}

ETF_SECTORS = {
    "XLE": "Energy", "XLF": "Financials", "KIE": "Financials",
    "XLK": "Information Technology", "XLI": "Industrials",
    "XLP": "Consumer Staples", "XLV": "Health Care", "XBI": "Health Care",
    "XLY": "Consumer Discretionary", "XLC": "Communication Services",
    "XLB": "Materials", "XLRE": "Real Estate", "XLU": "Utilities",
    "XOP": "Energy", "GDX": "Commodities", "GDXJ": "Commodities",
    "GLD": "Commodities", "AAAU": "Commodities", "PHYS": "Commodities",
    "SLV": "Commodities", "TLT": "Fixed Income", "SHY": "Fixed Income",
    "BIL": "Fixed Income", "HYG": "Fixed Income", "VOO": "Diversified Funds",
    "IVV": "Diversified Funds", "QQQ": "Diversified Funds",
    "IWM": "Diversified Funds",
}

def fetch(url):
    request = Request(url, headers=HEADERS)
    with urlopen(request, timeout=30) as response:
        return json.load(response)

def sic_sector(sic, description):
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return "Unclassified"
    text = (description or "").upper()
    if 2830 <= code <= 2839 or 3840 <= code <= 3859 or 8000 <= code <= 8099:
        return "Health Care"
    if code in (1311, 2911) or 1380 <= code <= 1389:
        return "Energy"
    if 4900 <= code <= 4999:
        return "Utilities"
    if 6500 <= code <= 6599:
        return "Real Estate"
    if 6000 <= code <= 6799:
        return "Financials"
    if 4800 <= code <= 4899 or 7800 <= code <= 7999:
        return "Communication Services"
    if 7370 <= code <= 7379 or 3570 <= code <= 3579 or 3600 <= code <= 3699:
        return "Information Technology"
    if 2000 <= code <= 2199 or 5400 <= code <= 5499 or code == 2840:
        return "Consumer Staples"
    if 2200 <= code <= 2399 or 2500 <= code <= 2599 or 3000 <= code <= 3199:
        return "Consumer Discretionary"
    if 5200 <= code <= 5999 or 7000 <= code <= 7299:
        return "Consumer Staples" if any(word in text for word in ("GROCERY", "FOOD", "DRUG")) else "Consumer Discretionary"
    if 1000 <= code <= 1499 or 2400 <= code <= 2829 or 2850 <= code <= 3399:
        return "Materials"
    if 3400 <= code <= 4799 or 5000 <= code <= 5199 or 8700 <= code <= 8799:
        return "Industrials"
    return "Other"

def fallback(row):
    symbol = (row.get("symbol") or "").upper()
    description = (row.get("description") or "").upper()
    if symbol in ETF_SECTORS:
        return ETF_SECTORS[symbol]
    if any(word in description for word in ("ETF", "INDEX", "TREASURY", "T-BILL")):
        return "Diversified Funds"
    if any(word in description for word in ("BANK", "FINANCIAL", "INSURANCE", "CAPITAL")):
        return "Financials"
    if any(word in description for word in ("PHARMA", "THERAPEUT", "HEALTH", "BIO", "MEDICAL")):
        return "Health Care"
    if any(word in description for word in ("SOFTWARE", "SEMICONDUCTOR", "TECHNOLOG")):
        return "Information Technology"
    if any(word in description for word in ("ENERGY", "OIL", "GAS", "PETROLEUM")):
        return "Energy"
    return "Unclassified"

def underlying(row):
    return ((row.get("description") or "").split() or [""])[0].upper()


def main():
    # Pass a saved /api/v1/report response: python3 build_sectors.py report.json
    report = json.loads(Path(sys.argv[1]).read_text())
    rows = report["positions"]
    symbols = {(row.get("symbol") or "").upper() for row in rows if row.get("symbol")}
    # Options and warrants take their underlying's sector; the description starts with its ticker.
    symbols |= {underlying(row) for row in rows if row.get("asset_category") in ("OPT", "WAR") and underlying(row)}
    symbols = sorted(symbols)
    screener = fetch(
        "https://api.nasdaq.com/api/screener/stocks"
        "?tableonly=true&limit=25&offset=0&download=true"
    )
    by_ticker = {
        item["symbol"].upper(): item
        for item in (screener.get("data", {}).get("rows") or [])
        if item.get("symbol")
    }
    names = {
        "Technology": "Information Technology",
        "Consumer Staples": "Consumer Staples",
        "Consumer Discretionary": "Consumer Discretionary",
        "Health Care": "Health Care",
        "Finance": "Financials",
        "Financials": "Financials",
        "Telecommunications": "Communication Services",
        "Basic Materials": "Materials",
        "Real Estate": "Real Estate",
        "Energy": "Energy",
        "Utilities": "Utilities",
        "Industrials": "Industrials",
        "Miscellaneous": "Other",
    }
    mapping, metadata = {}, {}
    for symbol in symbols:
        row = next((item for item in rows if (item.get("symbol") or "").upper() == symbol), {"symbol": symbol})
        if symbol in ETF_SECTORS:
            mapping[symbol] = ETF_SECTORS[symbol]
            metadata[symbol] = {"source": "fund_override"}
            continue
        lookup = symbol.replace(" ", "-")
        item = by_ticker.get(lookup) or by_ticker.get(symbol)
        if item and item.get("sector"):
            mapping[symbol] = names.get(item["sector"], item["sector"])
            metadata[symbol] = {
                "source": "Nasdaq screener",
                "industry": item.get("industry"),
            }
        else:
            mapping[symbol] = fallback(row)
            metadata[symbol] = {"source": "description"}
    output = {"generated_for": report["date"], "sectors": mapping, "metadata": metadata}
    (ROOT / "sectors.json").write_text(json.dumps(output, indent=2))
    counts = {}
    for value in mapping.values():
        counts[value] = counts.get(value, 0) + 1
    print(json.dumps({"symbols": len(symbols), "sector_counts": counts}, indent=2))

if __name__ == "__main__":
    main()
