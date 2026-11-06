#!/usr/bin/env python3
"""
gen_market_data.py -- synthetic HOSE-style tick dataset generator.

Generates a time-ordered consolidated tape of trade prints with best-bid /
best-ask snapshots as a single CSV.  Pure stdlib, no numpy/pandas needed.

Market model (per ticker, per session):
  * open price gaps off the previous close, prices walk on the exchange tick
    grid (HOSE: 10/50/100 VND steps) with a daily +/-7% band around the
    reference price,
  * step size follows a fat-tailed discrete ladder whose shape depends on a
    slowly mean-reverting "volatility regime" multiplier -> volatility
    clustering,
  * aggressor side (B/S) is biased by a persistent order-flow state, and the
    price direction in turn feeds that state: short-horizon return
    autocorrelation and order-flow imbalance carry real (testable) signal,
  * trade arrivals are inhomogeneous Poisson with a U-shaped intraday rate
    plus occasional Hawkes-style bursts (news),
  * trade sizes cluster (bursts of large prints), HOSE sizes are multiples
    of 100 (board lots); OTC names print odd sizes with wider spreads.

Output columns:
  ts_us         epoch microseconds (int64, globally sorted)
  trade_date    YYYY-MM-DD
  exchange      HOSE | OTC
  ticker
  ref_price     daily reference price = previous session close (VND)
  match_price   trade price (VND, tick-aligned on HOSE)
  match_volume  shares traded
  order_type    B | S   (aggressor side)
  bid_price     best bid at print time (VND)
  ask_price     best ask at print time (VND)
  bid_volume    depth resting at best bid (shares)
  ask_volume    depth resting at best ask (shares)

Usage:
  python gen_market_data.py                       # ~5 GB, 30 sessions
  python gen_market_data.py --target-gb 5         # same, explicit
  python gen_market_data.py --days 5 --rows-per-day 200000 --out tiny.csv
  python gen_market_data.py --verify out.csv      # sanity-check a file
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta

EPOCH = datetime(1970, 1, 1)
US = timedelta(microseconds=1)

# ---------------------------------------------------------------------------
# Instruments
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Instrument:
    ticker: str
    company: str
    ref: float      # anchor / first reference price (VND)
    vol: float      # relative activity (drives rows/sec, not just sigma)
    weight: float   # share of total prints


INSTRUMENTS = [
    Instrument("FPT", "Cong ty Co phan FPT",        135_000.0, 1.00, 0.13),
    Instrument("VCB", "Ngan hang Vietcombank",       92_000.0, 0.65, 0.17),
    Instrument("VHM", "Vinhomes",                    43_000.0, 1.60, 0.11),
    Instrument("HPG", "Tap doan Hoa Phat",           26_000.0, 2.30, 0.24),
    Instrument("VIC", "Tap doan Vingroup",           46_000.0, 0.90, 0.07),
    Instrument("MSN", "Tap doan Masan",              72_000.0, 0.55, 0.05),
    Instrument("SSI", "Cong ty CP Chung khoan SSI",  33_000.0, 2.70, 0.09),
    Instrument("MWG", "Dau tu The Gioi",             62_000.0, 0.85, 0.05),
    Instrument("TPB", "Ngan hang TMCP Tien Phong",   19_000.0, 1.90, 0.04),
    Instrument("CTG", "Ngan hang Cong thuong",       35_000.0, 1.20, 0.03),
    Instrument("VNM", "Cong ty Sua Viet Nam",        68_000.0, 0.50, 0.01),
    Instrument("REE", "Cong ty Co dien lanh",        62_000.0, 0.30, 0.01),
]

OTC_TICKERS = {"SSI", "MWG"}          # negotiated-dealing names

PRICE_MAX = 900_000_000
BAND = 0.07                            # HOSE daily price band (+/- 7%)

# ---------------------------------------------------------------------------
# Sessions: morning 09:00-11:30, afternoon 13:00-14:45
# ---------------------------------------------------------------------------

MORNING = (dtime(9, 0), dtime(11, 30))     # 150 min
AFTERNOON = (dtime(13, 0), dtime(14, 45))  # 105 min

# U-shaped intensity, one entry per minute of each session.
CURVE_M = [1.55 - 0.55 * (i / 44.0) for i in range(45)] + [1.0] * 105          # 150
CURVE_A = [1.0 + 0.85 * (i / 104.0) for i in range(105)]                       # 105
MASS_M = sum(CURVE_M) * 60.0          # intensity-mass in seconds
MASS_A = sum(CURVE_A) * 60.0

_HOLIDAYS = {(1, 1), (4, 30), (5, 1), (9, 2)} | {(2, d) for d in range(16, 23)}


def session_dates(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5 and (d.month, d.day) not in _HOLIDAYS:
            out.append(d)
        d += timedelta(days=1)
    return out


def epoch_us(d: date, t: dtime) -> int:
    return int((datetime.combine(d, t) - EPOCH) / US)


# ---------------------------------------------------------------------------
# Discrete ladders
# ---------------------------------------------------------------------------

def ladder_cdf(values, weights):
    total = sum(weights)
    cdf, acc = [], 0.0
    for w in weights:
        acc += w / total
        cdf.append(acc)
    cdf[-1] = 1.0
    return values, cdf


def step_ladder(p_zero: float, exponent: float):
    """|price change| in ticks. P(0)=p_zero, tail ~ 1/i^exponent."""
    vals = [0, 1, 2, 3, 4, 5, 6, 8, 10, 13, 17, 22, 30, 40, 55, 75, 100]
    w = [p_zero] + [(1.0 - p_zero) / (v ** exponent)
                    for v in vals[1:]]
    return ladder_cdf(vals, w)


LADDERS = [  # volatility regime -> step distribution
    step_ladder(0.62, 2.1),    # calm
    step_ladder(0.50, 1.6),    # normal
    step_ladder(0.36, 1.15),   # elevated
    step_ladder(0.22, 0.85),   # news / extreme
]

SIZE_LOT, SIZE_CDF = ladder_cdf(
    [100, 200, 300, 500, 700, 1_000, 1_500, 2_000, 3_000, 5_000,
     7_000, 10_000, 15_000, 20_000, 30_000, 50_000],
    [1.0 / math.sqrt(v / 100.0) for v in
     [100, 200, 300, 500, 700, 1_000, 1_500, 2_000, 3_000, 5_000,
      7_000, 10_000, 15_000, 20_000, 30_000, 50_000]])
SIZE_ODD, SIZE_ODD_CDF = ladder_cdf(
    [17, 53, 130, 340, 720, 1_500, 3_300, 6_700, 14_000, 28_000, 61_000],
    [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.22, 0.15, 0.1])
DEPTH, DEPTH_CDF = ladder_cdf(
    [100, 200, 400, 800, 1_500, 3_000, 6_000, 12_000, 25_000, 50_000,
     100_000, 200_000],
    [1.2, 1.1, 1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.22, 0.15])
SPREAD, SPREAD_CDF = ladder_cdf(          # half-spread in ticks
    [1, 1, 2, 2, 3, 4, 6, 9],
    [0.30, 0.30, 0.18, 0.18, 0.10, 0.06, 0.04, 0.02])
BAND_SNAP = 100.0


def tick_size(px: int) -> int:
    if px < 10_000:
        return 10
    if px < 50_000:
        return 50
    return 100


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

class TickerState:
    __slots__ = ("ticker", "idx", "is_otc", "ref", "px", "mom", "volm",
                 "burst_arr", "burst_sz", "last_close")

    def __init__(self, inst: Instrument, idx: int):
        self.ticker = inst.ticker
        self.idx = idx
        self.is_otc = inst.ticker in OTC_TICKERS
        self.ref = int(inst.ref)
        self.px = int(inst.ref)
        self.mom = 0.0
        self.volm = 1.0
        self.burst_arr = 0
        self.burst_sz = 0
        self.last_close = int(inst.ref)


def gen_session(day: date, afternoon: bool, rows_target: float, rng: random.Random,
                states: list[TickerState]) -> tuple[bytes, int]:
    """Simulate one trading session (morning or afternoon) for all tickers."""
    curve = CURVE_A if afternoon else CURVE_M
    t0, t1 = (AFTERNOON if afternoon else MORNING)
    start_us = epoch_us(day, t0)
    end_us = epoch_us(day, t1)
    dur_mass = MASS_A if afternoon else MASS_M

    day_mult = math.exp(0.30 * rng.gauss(0, 1))     # market-wide activity
    rows: list[tuple[int, int, str]] = []

    for st in states:
        # per-ticker per-session targets and regime draws
        lam = rows_target * INSTRUMENTS[st.idx].weight * day_mult \
            * math.exp(0.20 * rng.gauss(0, 1)) / dur_mass      # events/sec
        if lam <= 0:
            continue
        st.mom = 0.6 * st.mom
        st.volm = min(4.0, max(0.35, st.volm * math.exp(0.15 * rng.gauss(0, 1))))
        gap_open = -0.004 - 0.003 * rng.random()
        open_px = int(round(st.last_close * (1 + gap_open)))
        if st.is_otc:
            st.px = max(1000, open_px)
        else:
            ts0 = tick_size(open_px)
            st.px = max(1000, int(round(open_px / ts0)) * ts0)
        lo = int(st.ref * (1 - BAND))
        hi = int(st.ref * (1 + BAND))
        if not st.is_otc:                       # snap band edges to grid
            lo = int(round(lo / tick_size(lo))) * tick_size(lo)
            hi = int(round(hi / tick_size(hi))) * tick_size(hi)
        lo = max(1000, lo)
        hi = min(PRICE_MAX, hi)
        ref_str = str(st.ref)
        pre = ",%s,%s,%s," % (day.isoformat(),
                              "OTC" if st.is_otc else "HOSE", st.ticker)

        t = start_us + int(rng.expovariate(3.0) * 1e6)   # first print
        rand = rng.random
        gexp = rng.expovariate
        ggauss = rng.gauss
        bisect_r = bisect.bisect_right

        while t < end_us:
            # ---- arrivals (U-curve + bursts) ----
            minute = int((t - start_us) // 60_000_000)
            rate = lam * curve[minute if minute < len(curve) else -1]
            if st.burst_arr > 0:
                rate *= 8.0
                st.burst_arr -= 1
            elif rand() < 0.004:
                st.burst_arr = 20 + int(rand() * 400)
            # ---- volatility regime ----
            st.volm *= math.exp(0.010 * ggauss(0, 1))
            if st.volm < 0.35:
                st.volm = 0.35
            elif st.volm > 4.0:
                st.volm = 4.0
            vl, vc = (LADDERS[0] if st.volm < 0.7 else
                      LADDERS[1] if st.volm < 1.4 else
                      LADDERS[2] if st.volm < 2.5 else LADDERS[3])
            step = vl[bisect_r(vc, rand())]
            # ---- direction: momentum + band pullback ----
            p_up = 0.5 + 0.12 * math.tanh(st.mom) \
                - 0.045 * max(-1.0, min(1.0, (st.px - st.ref) / st.ref * 10.0))
            up = rand() < p_up
            tsz = 10 if st.is_otc else tick_size(st.px)
            npx = st.px + (tsz * step if up else -tsz * step)
            if npx > hi:
                npx = hi
            elif npx < lo:
                npx = lo
            changed = npx != st.px
            st.px = npx
            # ---- aggressor side (tick-test-consistent ~88%) ----
            if changed:
                side = "B" if (up and rand() < 0.88) or (not up and rand() >= 0.88) else "S"
            else:
                side = "B" if rand() < 0.5 + 0.18 * math.tanh(st.mom) else "S"
            ssign = 1.0 if side == "B" else -1.0
            st.mom = 0.93 * st.mom + 0.15 * ssign + (0.22 if (up and changed) else (-0.22 if changed else 0.0))
            # ---- size (lot ladder on HOSE, odd sizes OTC, size bursts) ----
            if st.burst_sz > 0:
                st.burst_sz -= 1
                szmul = 5
            elif rand() < 0.08:
                st.burst_sz = 3 + int(rand() * 25)
                szmul = 5
            else:
                szmul = 1
            if st.is_otc:
                vol = SIZE_ODD[bisect_r(SIZE_ODD_CDF, rand())] * szmul
            else:
                vol = SIZE_LOT[bisect_r(SIZE_CDF, rand())] * szmul
            # ---- quotes around the last price ----
            spt = SPREAD[bisect_r(SPREAD_CDF, rand())] * (3 if st.is_otc else 1)
            bid = st.px - spt * tsz
            ask = st.px + spt * tsz + tsz          # ask >= px + 1 tick
            bvi = bisect_r(DEPTH_CDF, rand())
            avi = bisect_r(DEPTH_CDF, rand())
            if side == "B":                        # buyer lifted the ask
                avi = max(0, avi - 1)
                ask = st.px + max(spt, 1) * tsz
            else:
                bvi = max(0, bvi - 1)
            bvol = DEPTH[bvi]
            avol = DEPTH[avi]
            if bid < 1000:
                bid = max(900, st.px - tsz)
            rows.append((t, st.idx,
                         "%d%s%d,%d,%d,%s,%d,%d,%d,%d,%d" %
                         (t, pre, st.ref, st.px, vol, side,
                          bid, ask, bvol, avol, 0) if False else
                         "%d%s%d,%d,%d,%s,%d,%d,%d,%d" %
                         (t, pre, st.ref, st.px, vol, side, bid, ask, bvol, avol)))
            # ---- advance clock ----
            t += int(gexp(rate) * 1e6)

    rows.sort()
    st.last_close = st.px  # placeholder, fixed by caller loop below
    return b"\n".join(r[2].encode("ascii") for r in rows), len(rows)
