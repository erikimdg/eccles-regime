"""Causal, configuration-driven features. Never fill missing observations."""
from functools import lru_cache

import exchange_calendars as xc
import numpy as np
import pandas as pd

OPERATORS = {"return", "volatility", "level", "correlation", "spread", "pc1_share"}


@lru_cache(maxsize=48)
def sessions(calendar, start, end):
    return xc.get_calendar(calendar, start=start, end=end).sessions


def prepare_prices(raw, sources, start, end):
    prepared = {}
    for spec in sources:
        key = spec["id"]
        s = raw[key].copy().sort_index()
        if not s.index.is_unique or not isinstance(s.index, pd.DatetimeIndex):
            raise ValueError(f"Invalid date index: {key}")
        if s.index.tz is not None or not s.index.equals(s.index.normalize()):
            raise ValueError(f"Non-session dates: {key}")
        present = s.dropna().to_numpy(dtype=float)
        if not np.isfinite(present).all() or (present <= 0).any():
            raise ValueError(f"Invalid price: {key}")
        s = s.loc[start:end]
        if spec["calendar"] != "published":
            s = s.reindex(sessions(spec["calendar"], start, end))
        prepared[key] = s
    return prepared


def simple_return(p, h):
    return (p / p.shift(h) - 1).where(p.rolling(h + 1).count() == h + 1)


def build_features(prices, spec):
    """Native calendars for core features; MSCI prices align to US sessions first."""
    anchor = prices[spec["anchor_source"]].index
    source_specs = {s["id"]: s for s in spec["sources"]}
    p = {k: s.reindex(anchor) if source_specs[k].get("align") == "anchor" else s
         for k, s in prices.items()}
    raw, inputs = {}, {}
    for f in spec["features"]:
        op, ids = f["operator"], f["sources"]
        h = f.get("window", 1)
        if op == "return":
            s = simple_return(p[ids[0]], h)
        elif op == "volatility":
            r = np.log(p[ids[0]] / p[ids[0]].shift(1))
            s = r.rolling(h, min_periods=h).std(ddof=0) * np.sqrt(252)
        elif op == "level":
            s = p[ids[0]]
        elif op == "correlation":
            pair = pd.concat([np.log(p[k] / p[k].shift(1)) for k in ids], axis=1)
            s = pair.iloc[:, 0].rolling(h, min_periods=h).corr(pair.iloc[:, 1])
        elif op == "spread":
            s = simple_return(p[ids[0]], h) - simple_return(p[ids[1]], h)
        elif op == "pc1_share":
            panel = pd.concat([p[k].reindex(anchor) for k in ids], axis=1)
            returns = panel.pct_change(fill_method=None).to_numpy()
            values = np.full(len(panel), np.nan)
            for i in range(h, len(panel)):
                a = returns[i-h+1:i+1]
                if np.isfinite(a).all() and (a.std(axis=0) > 0).all():
                    corr = np.corrcoef(a, rowvar=False)
                    values[i] = np.linalg.eigvalsh(corr)[-1] / np.trace(corr)
            s = pd.Series(values, index=anchor)
        else:
            raise ValueError(f"Unsupported operator: {op}")
        raw[f["id"]] = s.reindex(anchor)
        rank = f.get("rank_window")
        inputs[f["id"]] = (s.rolling(rank, min_periods=rank).rank(method="average", pct=True)
                               if rank else s).reindex(anchor)
    return pd.DataFrame(inputs, index=anchor), pd.DataFrame(raw, index=anchor)


def expected_session(now=None, grace_minutes=90):
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if now.tz is None:
        raise ValueError("Clock must include a timezone")
    start, end = (now - pd.Timedelta(days=20)).date().isoformat(), now.date().isoformat()
    cal = xc.get_calendar("XNYS", start=start, end=end)
    eligible = cal.schedule.index[(cal.schedule["close"] + pd.Timedelta(minutes=grace_minutes)) <= now]
    if not len(eligible):
        raise ValueError("No completed session in clock window")
    return eligible[-1].date().isoformat()
