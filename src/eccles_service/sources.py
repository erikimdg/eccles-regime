"""Identified original daily series, bounded sequential requests and snapshots."""
import csv
import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, quote, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

class SourceError(RuntimeError):
    pass


# Faster real channels for the same datum: the official file stays authoritative,
# a fallback may only extend it past its last date, never replace or estimate it.
FALLBACKS = {"vix": dict(provider="fmp", symbol="^VIX")}
FALLBACK_TOLERANCE = 0.005
FALLBACK_MIN_OVERLAP = 3


def msci_homepage_extension(source, primary, end, cache_dir=None):
    """Extend the same original index from a dated, verified homepage capture.

    Browser capture is a separate ingestion step. This does not solve or bypass
    the provider's browser challenge. Never carry a prior-day file forward.
    """
    if source['provider'] != 'msci' or primary.index.max() >= pd.Timestamp(end):
        return primary, None
    cache = Path(cache_dir) if cache_dir is not None else Path(os.environ.get('ECCLES_RUNTIME', 'runtime')) / 'msci-homepage'
    path = cache / (end + '.json')
    if not path.exists():
        return primary, None
    if path.is_symlink() or path.stat().st_size > 1_000_000:
        raise SourceError('Invalid homepage capture file')
    try:
        blob = path.read_bytes(); obj = json.loads(blob)
        if obj['schema'] != 'msci_homepage_v1' or obj['as_of'] != end:
            raise ValueError('date or schema')
        if obj['currency'] != source['currency'] or obj['variant'] != source['variant']:
            raise ValueError('variant or currency')
        observed = datetime.fromisoformat(obj['observed_at'])
        if observed.tzinfo is None or observed.timestamp() > datetime.now(timezone.utc).timestamp() + 60:
            raise ValueError('observation time')
        if observed.date().isoformat() < end:
            raise ValueError('observation precedes value date')
        rows = obj['rows']; codes = [str(row['index_code']) for row in rows]
        if len(codes) != len(set(codes)):
            raise ValueError('duplicate identity')
        matches = [row for row in rows if str(row['index_code']) == str(source['symbol'])]
        if not matches:
            return primary, None
        row = matches[0]; value = row['level']
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
            raise ValueError('value')
        url = urlparse(row['url'])
        if url.scheme != 'https' or url.netloc != 'www.msci.com' or not url.path.startswith('/indexes'):
            raise ValueError('origin')
        if row['precision_decimals'] != 2 or obj['capture_method'] != 'verified_browser_display':
            raise ValueError('unsupported capture method')
    except (KeyError, ValueError, TypeError, UnicodeError, AttributeError) as ex:
        raise SourceError('Invalid dated MSCI homepage capture: ' + type(ex).__name__) from None
    extra = pd.Series([float(value)], index=pd.DatetimeIndex([end]), dtype=float)
    merged = pd.concat([primary, extra]).sort_index()
    record = dict(provider='msci_homepage', symbol=str(source['symbol']), url=row['url'],
                  dates=[end], sha256=hashlib.sha256(blob).hexdigest(), observed_at=obj['observed_at'],
                  precision_decimals=2, capture_method=obj['capture_method'],
                  observation_window_kst=obj.get('observation_window_kst'),
                  automatic_browser_collection=False)
    return merged, dict(record=record, blob=blob)


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


def fmp_rows(symbol, start, end):
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
    return [(r["date"], r["close"]) for r in obj], blob, url


def trailing_fallback(source_id, primary, end):
    """Extend an official series with the identical datum from a faster distributor.

    Applies only to sources listed in FALLBACKS, only for dates strictly after the
    official file's last date, and only when both channels agree within
    FALLBACK_TOLERANCE on at least FALLBACK_MIN_OVERLAP recent overlapping dates.
    Returns (series, None) untouched when no fallback applies, or
    (merged, dict(record=..., blob=...)) when dates were added.
    """
    fb = FALLBACKS.get(source_id)
    if not fb or primary.index.max() >= pd.Timestamp(end):
        return primary, None
    probe_start = (primary.index.max() - pd.Timedelta(days=21)).date().isoformat()
    rows, blob, url = fmp_rows(fb["symbol"], probe_start, end)
    values = {}
    for day, value in rows:
        day = pd.Timestamp(day).normalize()
        if day in values:
            raise SourceError("Duplicate fallback date")
        if value is not None and value not in ("", ".", "NA"):
            values[day] = float(value)
    alt = pd.Series(values, dtype=float).sort_index()
    overlap = primary.index.intersection(alt.index)
    if len(overlap) < FALLBACK_MIN_OVERLAP:
        raise SourceError("Fallback overlap insufficient")
    if (primary.loc[overlap] - alt.loc[overlap]).abs().max() > FALLBACK_TOLERANCE:
        raise SourceError("Fallback disagrees with official history")
    extra = alt[(alt.index > primary.index.max()) & (alt.index <= pd.Timestamp(end))]
    if extra.empty:
        return primary, None
    if not np.isfinite(extra).all() or (extra <= 0).any():
        raise SourceError("Nonfinite or nonpositive fallback values")
    merged = pd.concat([primary, extra]).sort_index()
    record = dict(provider=fb["provider"], symbol=fb["symbol"], url=url,
                  dates=[d.date().isoformat() for d in extra.index],
                  sha256=hashlib.sha256(blob).hexdigest(),
                  observed_at=datetime.now(timezone.utc).isoformat())
    return merged, dict(record=record, blob=blob)


def acquire_one(source, start, end):
    provider, symbol = source["provider"], source["symbol"]
    if provider == "fmp":
        rows, blob, url = fmp_rows(symbol, start, end)
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
    msci_download_cache = {}
    msci_web_cache = {}
    for source in spec["sources"]:
        key = source["id"]
        try:
            s, blob, url = acquire_one(source, start, end)
            fallback = None
            if key in FALLBACKS:
                try:
                    s, fallback = trailing_fallback(key, s, end)
                except SourceError as ex:
                    # The official series stands on its own; a fallback problem is recorded, never fatal.
                    fallback = dict(record=dict(error=str(ex)))
            elif source['provider'] == 'msci':
                from .msci_download import extend
                from .msci_web import extend as web_extend
                download_error = None
                web_error = None
                try:
                    s, fallback = web_extend(source, s, end, msci_web_cache,
                                             os.environ.get('ECCLES_RUNTIME', 'runtime'))
                except Exception as ex:
                    web_error = str(ex) if isinstance(ex, ValueError) else type(ex).__name__
                if fallback is None:
                    try:
                        s, fallback = extend(source, s, end, msci_download_cache, fetch)
                    except Exception as ex:
                        download_error = str(ex) if isinstance(ex, ValueError) else type(ex).__name__
                if fallback is None:
                    try:
                        s, fallback = msci_homepage_extension(source, s, end)
                    except SourceError as ex:
                        fallback = dict(record=dict(provider='msci_homepage', error=str(ex)))
                if download_error:
                    fallback = fallback or dict(record={})
                    fallback['record']['public_download_error'] = download_error
                if web_error:
                    fallback = fallback or dict(record={})
                    fallback['record']['homepage_error'] = web_error
            # Only provider response bodies are persisted. Never save request query credentials.
            raw_path = destination / (key + ".raw")
            raw_path.write_bytes(blob)
            s.rename("close").to_csv(destination / (key + ".csv"), index_label="date")
            prices[key] = s
            record = dict(source=key, provider=source["provider"], symbol=source["symbol"], url=url,
                          first=s.index.min().date().isoformat(), last=s.index.max().date().isoformat(), n=len(s),
                          sha256=hashlib.sha256(blob).hexdigest(), observed_at=datetime.now(timezone.utc).isoformat())
            if fallback:
                record["fallback"] = fallback["record"]
                if "blob" in fallback:
                    (destination / (key + ".fallback.raw")).write_bytes(fallback["blob"])
            records.append(record)
        except Exception as ex:
            errors.append(dict(source=key, error=str(ex) if isinstance(ex, SourceError) else type(ex).__name__))
    manifest = dict(start=start, end=end, records=records, errors=errors)
    if msci_web_cache:
        from .msci_web import present_codes
        meta = {'error':msci_web_cache.get('error')}
        if 'capture' in msci_web_cache:
            cap=msci_web_cache['capture']
            meta.update(observed_at=cap['observed_at'], target=end,
                        target_present=present_codes(cap), sha256=cap['sha256'])
        # Preserve raw target and overlap observations even when validation fails.
        (destination/'msci-homepage-evidence.json').write_text(json.dumps(msci_web_cache,indent=2)+'\n')
        manifest['msci_homepage_api'] = meta
    if msci_download_cache:
        meta = {k:v for k,v in msci_download_cache.items() if k not in {'data','blob'}}
        if 'blob' in msci_download_cache:
            blob = msci_download_cache['blob']
            (destination / 'msci-public-download.raw').write_bytes(blob)
            meta['sha256'] = hashlib.sha256(blob).hexdigest()
        if 'data' in msci_download_cache:
            meta['source_dates'] = {code:s.index.max().date().isoformat() for code,s in msci_download_cache['data'].items()}
            meta['target_present'] = {code:pd.Timestamp(end) in s.index for code,s in msci_download_cache['data'].items()}
        manifest['msci_public_download'] = meta
    (destination / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return prices, manifest
