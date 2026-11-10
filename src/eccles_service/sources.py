"""Identified original daily series, bounded sequential requests and snapshots."""
import csv
import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

class SourceError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, params=None):
    target = url + ("?" + urlencode(params) if params else "")
    req = Request(target, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/csv,text/plain,*/*"})
    try:
        with build_opener(NoRedirect()).open(req, timeout=45) as response:
            blob = response.read(8_000_001)
        if len(blob) > 8_000_000:
            raise SourceError("Response exceeds source limit")
        return blob
    except SourceError:
        raise
    except Exception as ex:
        # Exception strings from HTTP clients may contain credential-bearing URLs.
        status = getattr(ex, "code", None)
        raise SourceError(f"HTTP source failed: {type(ex).__name__}, status={status}") from None


def series(rows, start, end):
    values = {}
    for day, value in rows:
        day = pd.Timestamp(day).normalize()
        if day < pd.Timestamp(start) or day > pd.Timestamp(end):
            continue
        if day in values:
            raise SourceError("Duplicate source date")
        if value is None or value in ("", ".", "NA"):
            continue
        values[day] = float(value)
    s = pd.Series(values, dtype=float).sort_index()
    if len(s) < 300 or not np.isfinite(s).all() or (s <= 0).any():
        raise SourceError("Insufficient, nonfinite or nonpositive original history")
    return s


def acquire_one(source, start, end):
    provider, symbol = source["provider"], source["symbol"]
    if provider == "fmp":
        key = os.environ.get("FMP_API_KEY")
        if not key:
            raise SourceError("FMP credential unavailable")
        url = "https://financialmodelingprep.com/stable/historical-price-eod/full"
        blob = fetch(url, dict(symbol=symbol, **{"from": start, "to": end}, apikey=key))
        obj = json.loads(blob)
        if not isinstance(obj, list):
            raise SourceError("Unexpected FMP response schema")
        if any(r.get("symbol", symbol) != symbol for r in obj):
            raise SourceError("FMP identity mismatch")
        rows = [(r["date"], r["close"]) for r in obj]
    elif provider == "yahoo":
        url = "https://query1.finance.yahoo.com/v8/finance/chart/" + quote(symbol, safe="")
        blob = fetch(url, dict(period1=int(pd.Timestamp(start, tz="UTC").timestamp()),
                               period2=int((pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)).timestamp()), interval="1d", events="div,splits"))
        obj = json.loads(blob)["chart"]["result"][0]
        if obj["meta"]["symbol"] != symbol:
            raise SourceError("Yahoo identity mismatch")
        tz = ZoneInfo(obj["meta"]["exchangeTimezoneName"])
        rows = [(datetime.fromtimestamp(t, timezone.utc).astimezone(tz).date(), v)
                for t, v in zip(obj["timestamp"], obj["indicators"]["quote"][0]["close"])]
    elif provider in {"cboe", "stoxx"}:
        if provider == "cboe":
            url, df, vf, fmt, sep = "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv", "DATE", "CLOSE", "%m/%d/%Y", ","
        else:
            url, df, vf, fmt, sep = "https://www.stoxx.com/document/Indices/Current/HistoricalData/h_v2tx.txt", "Date", "Indexvalue", "%d.%m.%Y", ";"
        blob = fetch(url)
        rows = [(datetime.strptime(r[df], fmt).date(), r[vf]) for r in csv.DictReader(io.StringIO(blob.decode("utf-8-sig")), delimiter=sep)]
    elif provider == "msci":
        url = "https://app2.msci.com/products/service/index/indexmaster/getLevelDataForGraph"
        blob = fetch(url, dict(index_codes=symbol, currency_symbol=source["currency"], index_variant=source["variant"], data_frequency="DAILY", start_date=start.replace("-", ""), end_date=end.replace("-", "")))
        obj = json.loads(blob)
        # Provider metadata sits within indexes, as well as in some historic top-level responses.
        metadata = obj.get("indexes", obj)
        for field, expected in [("msci_index_code", symbol), ("index_variant_type", source["variant"]), ("ISO_currency_symbol", source["currency"])]:
            actual = metadata.get(field, obj.get(field))
            if str(actual) != str(expected):
                raise SourceError(f"MSCI identity mismatch: {field}")
        rows = [(datetime.strptime(str(r["calc_date"]), "%Y%m%d").date(), r["level_eod"]) for r in obj["indexes"]["INDEX_LEVELS"]]
    else:
        raise SourceError("Unsupported provider")
    return series(rows, start, end), blob, url


def acquire(spec, start, end, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    prices, records, errors = {}, [], []
    for source in spec["sources"]:
        key = source["id"]
        try:
            s, blob, url = acquire_one(source, start, end)
            # Only provider response bodies are persisted. Never save request query credentials.
            raw_path = destination / (key + ".raw")
            raw_path.write_bytes(blob)
            s.rename("close").to_csv(destination / (key + ".csv"), index_label="date")
            prices[key] = s
            records.append(dict(source=key, provider=source["provider"], symbol=source["symbol"], url=url,
                                first=s.index.min().date().isoformat(), last=s.index.max().date().isoformat(), n=len(s),
                                sha256=hashlib.sha256(blob).hexdigest(), observed_at=datetime.now(timezone.utc).isoformat()))
        except Exception as ex:
            errors.append(dict(source=key, error=str(ex) if isinstance(ex, SourceError) else type(ex).__name__))
    manifest = dict(start=start, end=end, records=records, errors=errors)
    (destination / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return prices, manifest
