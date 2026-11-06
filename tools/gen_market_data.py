#!/usr/bin/env python3
"""
gen_market_data.py -- synthetic HOSE-style tick dataset generator.

Writes a time-ordered consolidated tape of trade prints with top-of-book
snapshots as one CSV. Pure stdlib; no numpy/pandas required. At default
settings it produces ~100M prints / ~5 GB in a few minutes.

Market model (per ticker, per session):
  * open price gaps off the previous close; prices walk the exchange tick
    grid (HOSE: 10 / 50 / 100 VND steps) inside the daily +/-7% band,
  * step size follows a fat-tailed discrete ladder chosen by a slowly
    mean-reverting volatility-regime multiplier -> volatility clustering,
  * the aggressor side (B/S) is biased by a persistent order-flow state and
    price direction feeds that state back, so tick-rule consistency is high
    (~88%) and one-step returns carry mild negative autocorrelation,
  * arrivals are inhomogeneous Poisson under a U-shaped intraday intensity,
    plus Hawkes-style self-exciting bursts (news),
  * order sizes cluster; HOSE prints are board lots (multiples of 100), OTC
    names print odd sizes with wider spreads,
  * best bid / ask straddle the last trade by a tick-ladder half-spread;
    bid <= match_price < ask_price on every HOSE row.

Output columns:
  ts_us         epoch microseconds (int64, UTC; globally sorted)
  trade_date    YYYY-MM-DD (Vietnam session date, UTC+7)
  exchange      HOSE | OTC
  ticker
  ref_price     daily reference price = previous session close (VND)
  match_price   trade price (VND; tick-aligned on HOSE)
  match_volume  shares traded
  order_type    B | S   (aggressor side)
  bid_price     best bid at print time (VND)
  ask_price     best ask at print time (VND)
  bid_volume    depth resting at best bid (shares)
  ask_volume    depth resting at best ask (shares)

Usage:
  python gen_market_data.py                       # ~5 GB, 30 sessions
  python gen_market_data.py --target-gb 0.2 --sessions 3 --out tiny.csv
  python gen_market_data.py --verify out.csv      # sanity-check a file
"""

from __future__ import annotations

import argparse
import bisect
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
    vol: float      # relative activity (row share before weight)
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

BURST_P = 0.0015                       # prob a print seeds a news burst
BURST_LEN = 25                         # burst fires 25-50 prints
BURST_MULT = 8.0                       # burst arrival multiplier

# ---------------------------------------------------------------------------
# Sessions: morning 09:00-11:30, afternoon 13:00-14:45 (Vietnam time, UTC+7)
# ---------------------------------------------------------------------------

MORNING = (dtime(9, 0), dtime(11, 30))     # 150 min
AFTERNOON = (dtime(13, 0), dtime(14, 45))  # 105 min

# U-shaped intensity, one entry per minute of each session.
CURVE_M = [1.55 - 0.55 * (i / 44.0) for i in range(45)] + [1.0] * 105          # 150
CURVE_A = [1.0 + 0.85 * (i / 104.0) for i in range(105)]                       # 105
MASS_M = sum(CURVE_M) * 60.0          # intensity-mass in seconds
MASS_A = sum(CURVE_A) * 60.0
TZ_OFFSET_US = 7 * 3600 * 1_000_000   # Vietnam is UTC+7 all year

_HOLIDAYS = {(1, 1), (4, 30), (5, 1), (9, 2)} | {(2, d) for d in range(16, 23)}


def session_dates(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5 and (d.month, d.day) not in _HOLIDAYS:
            out.append(d)
        d += timedelta(days=1)
    return out


def epoch_us(d: date, t: dtime) -> int:
    """Epoch microseconds for a Vietnam-local wall-clock time."""
    return int((datetime.combine(d, t) - EPOCH) / US) - TZ_OFFSET_US


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
    w = [p_zero] + [(1.0 - p_zero) / (v ** exponent) for v in vals[1:]]
    return ladder_cdf(vals, w)


LADDERS = [  # volatility regime -> step distribution
    step_ladder(0.62, 2.1),    # calm
    step_ladder(0.50, 1.6),    # normal
    step_ladder(0.36, 1.15),   # elevated
    step_ladder(0.22, 0.85),   # news / extreme
]

_SIZE_VALS = [100, 200, 300, 500, 700, 1_000, 1_500, 2_000, 3_000, 5_000,
              7_000, 10_000, 15_000, 20_000, 30_000, 50_000]
SIZE_LOT, SIZE_CDF = ladder_cdf(
    _SIZE_VALS, [1.0 / math.sqrt(v / 100.0) for v in _SIZE_VALS])
SIZE_ODD, SIZE_ODD_CDF = ladder_cdf(
    [17, 53, 130, 340, 720, 1_500, 3_300, 6_700, 14_000, 28_000, 61_000],
    [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.22, 0.15, 0.1])
DEPTH, DEPTH_CDF = ladder_cdf(
    [100, 200, 400, 800, 1_500, 3_000, 6_000, 12_000, 25_000, 50_000,
     100_000, 200_000],
    [1.2, 1.1, 1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.22, 0.15])
SPREAD, SPREAD_CDF = ladder_cdf(          # half-spread in ticks
    [1, 2, 2, 3, 4, 6, 9],
    [0.30, 0.30, 0.18, 0.10, 0.06, 0.04, 0.02])


def tick_size(px: int) -> int:
    if px < 10_000:
        return 10
    if px < 50_000:
        return 50
    return 100


def snap(px: int) -> int:
    t = tick_size(px)
    return int(round(px / t)) * t


# ---------------------------------------------------------------------------
# Per-ticker state (survives across sessions / days)
# ---------------------------------------------------------------------------

class TickerState:
    __slots__ = ("ticker", "idx", "is_otc", "ref", "px", "mom", "volm",
                 "burst_arr", "burst_sz", "last_close")

    def __init__(self, inst: Instrument, idx: int):
        self.ticker = inst.ticker
        self.idx = idx
        self.is_otc = inst.ticker in OTC_TICKERS
        self.ref = snap(int(inst.ref))
        self.px = self.ref
        self.mom = 0.0
        self.volm = 1.0
        self.burst_arr = 0
        self.burst_sz = 0
        self.last_close = self.ref


# ---------------------------------------------------------------------------
# Session simulation
# ---------------------------------------------------------------------------

def gen_session(day: date, afternoon: bool, nominal: float,
                rng: random.Random, states: list[TickerState],
                day_mult: float) -> tuple[str, int]:
    """Simulate one session (morning or afternoon) for every ticker.

    Returns (text_body, n_rows). `nominal` is the requested base arrival
    count; emitted rows are ~nominal * uplift.
    """
    curve = CURVE_A if afternoon else CURVE_M
    t0, t1 = (AFTERNOON if afternoon else MORNING)
    start_us = epoch_us(day, t0)
    end_us = epoch_us(day, t1)
    dur_mass = MASS_A if afternoon else MASS_M
    seconds = (end_us - start_us) / 1e6
    mean_curve = dur_mass / seconds
    n_minutes = len(curve)

    activity = [(i.vol ** 0.88) * i.weight for i in INSTRUMENTS]
    act_total = sum(activity)

    rows: list[tuple[int, int, str]] = []
    date_str = day.isoformat()

    for st in states:
        lam = nominal * (activity[st.idx] / act_total) * day_mult / (seconds * mean_curve)
        if lam <= 0:
            continue
        st.mom = 0.6 * st.mom
        st.volm = min(4.0, max(0.35, st.volm * math.exp(0.15 * rng.gauss(0, 1))))
        gap_open = -0.004 - 0.003 * rng.random()
        st.px = max(1000, int(round(st.last_close * (1 + gap_open))))
        if not st.is_otc:
            st.px = max(1000, snap(st.px))
        lo = int(st.ref * (1 - BAND))
        hi = int(st.ref * (1 + BAND))
        if not st.is_otc:                       # snap band edges inward to grid
            lo = -(-lo // tick_size(lo)) * tick_size(lo)
            hi = (hi // tick_size(hi)) * tick_size(hi)
        lo, hi = max(1000, lo), min(PRICE_MAX, hi)
        st.px = min(hi, max(lo, st.px))
        pre = ",%s,%s,%s," % (date_str, "OTC" if st.is_otc else "HOSE", st.ticker)

        t = start_us + int(rng.expovariate(3.0) * 1e6)   # first print
        rand = rng.random
        gauss = rng.gauss
        expo = rng.expovariate
        b_right = bisect.bisect_right

        while t < end_us:
            # ---- next arrival: exact thinning under the U-shaped intensity ----
            minute = int((t - start_us) // 60_000_000)
            if minute >= n_minutes:
                break
            nxt = t + int(expo(lam * curve[minute] *
                               (BURST_MULT if st.burst_arr > 0 else 1.0)) * 1e6)
            while (nxt - start_us) // 60_000_000 != minute and nxt < end_us:
                minute = int((nxt - start_us) // 60_000_000)
                if minute >= n_minutes:
                    break
                mult = BURST_MULT if st.burst_arr > 0 else 1.0
                nxt = start_us + minute * 60_000_000 \
                    + int(expo(lam * curve[minute] * mult) * 1e6)
            if st.burst_arr > 0:
                st.burst_arr -= 1

            # ---- volatility regime ----
            st.volm = min(4.0, max(0.35, st.volm * math.exp(0.010 * gauss(0, 1))))
            vl, vc = (LADDERS[0] if st.volm < 0.7 else
                      LADDERS[1] if st.volm < 1.4 else
                      LADDERS[2] if st.volm < 2.5 else LADDERS[3])
            step = vl[b_right(vc, rand())]

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

            # ---- aggressor side (tick-test consistent ~88%) ----
            if changed:
                side = "B" if (up and rand() < 0.88) or (not up and rand() >= 0.88) else "S"
            else:
                side = "B" if rand() < 0.5 + 0.18 * math.tanh(st.mom) else "S"
            st.mom = 0.93 * st.mom + 0.15 * (1.0 if side == "B" else -1.0) \
                + (0.22 if up and changed else (-0.22 if changed else 0.0))

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
                vol = SIZE_ODD[b_right(SIZE_ODD_CDF, rand())] * szmul
            else:
                vol = SIZE_LOT[b_right(SIZE_CDF, rand())] * szmul

            # ---- top of book straddling the print ----
            spt = SPREAD[b_right(SPREAD_CDF, rand())] * (3 if st.is_otc else 1)
            bid = st.px - spt * tsz
            ask = st.px + max(spt, 1) * tsz
            bvi = max(0, b_right(DEPTH_CDF, rand()) - (1 if side == "S" else 0))
            avi = max(0, b_right(DEPTH_CDF, rand()) - (1 if side == "B" else 0))
            if bid < 1000:
                bid = max(900, st.px - tsz)

            rows.append((t, st.idx,
                         "%d%s%d,%d,%d,%s,%d,%d,%d,%d"
                         % (t, pre, st.ref, st.px, vol, side,
                            bid, ask, DEPTH[bvi], DEPTH[avi])))

            if st.burst_arr == 0 and rand() < BURST_P:
                st.burst_arr = BURST_LEN + int(rand() * BURST_LEN)

            if nxt <= t:
                nxt = t + 1
            t = nxt

        st.last_close = st.px

    rows.sort()
    return "\n".join(r[2] for r in rows), len(rows)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def measure(seed: int, nominal_per_day: float, days: list[date]) -> tuple[float, float]:
    """Return (emitted/nominal ratio, bytes per emitted row)."""
    states = [TickerState(inst, k) for k, inst in enumerate(INSTRUMENTS)]
    rng = random.Random(seed)
    emitted = total_bytes = 0
    for d in days:
        for afternoon in (False, True):
            text, n = gen_session(d, afternoon, nominal_per_day / 2, rng, states, 1.0)
            emitted += n
            total_bytes += len(text) + 1
    nominal = nominal_per_day * len(days)
    return emitted / max(nominal, 1), total_bytes / max(emitted, 1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

HEADER = ("ts_us,trade_date,exchange,ticker,ref_price,match_price,"
          "match_volume,order_type,bid_price,ask_price,bid_volume,ask_volume\n")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate a synthetic HOSE-style tick CSV (~5 GB by default).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--out", default="market_ticks.csv")
    ap.add_argument("--target-gb", type=float, default=5.0)
    ap.add_argument("--sessions", type=int, default=30)
    ap.add_argument("--rows-per-day", type=int, default=0,
                    help="override the auto-calibrated nominal prints/day")
    ap.add_argument("--start", default="2026-08-03")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--verify", metavar="CSV", default=None,
                    help="verify an existing file instead of generating")
    args = ap.parse_args()

    if args.verify:
        return verify(args.verify)

    start = date.fromisoformat(args.start)
    days = session_dates(start, args.sessions)
    t0 = time.perf_counter()
    target_bytes = args.target_gb * 1e9

    if args.rows_per_day > 0:
        calib, bpr = measure(args.seed, 50_000, session_dates(start, 2))
    else:
        # Probe at production-scale density, then solve for the rows/day that
        # lands on the size target.
        probe = session_dates(start, 4)
        guess = int(target_bytes / 120.0 / args.sessions)
        calib, bpr = measure(args.seed, guess, probe)
        args.rows_per_day = max(1, int(target_bytes / bpr / args.sessions / calib))
        print(f"  calibration: uplift {calib:.3f}, {bpr:.1f} B/row -> "
              f"{args.rows_per_day:,} nominal prints/day")

    print(f"Generating {args.out}: ~{args.target_gb:.1f} GB, {len(days)} sessions, "
          f"{args.rows_per_day:,} nominal prints/day, start {args.start}, seed {args.seed}")

    rnd = random.Random(args.seed)
    states = [TickerState(inst, k) for k, inst in enumerate(INSTRUMENTS)]
    written = 0
    target_per_day = args.rows_per_day

    with open(args.out, "w", newline="", encoding="ascii", buffering=1 << 23) as f:
        f.write(HEADER)
        size = len(HEADER)
        for di, d in enumerate(days):
            day_mult = math.exp(0.12 * rnd.gauss(0, 1))   # one per day, both sessions
            prev_total, day_bytes = written, 0
            for afternoon in (False, True):
                share = (MASS_A if afternoon else MASS_M) / (MASS_M + MASS_A)
                text, n = gen_session(d, afternoon, target_per_day * share,
                                      rnd, states, day_mult)
                f.write(text)
                f.write("\n")
                day_bytes += len(text) + 1
                written += n
            size += day_bytes
            # Closed-loop retarget: correct half the observed drift on what is
            # left, so the final size lands within a few percent of target.
            if di < len(days) - 1 and written and target_bytes > 0:
                per_row = size / written
                projected = size + (len(days) - di - 1) * per_row * target_per_day * calib
                drift = target_bytes / max(projected, 1) - 1.0
                target_per_day = max(1, int(target_per_day * (1.0 + 0.5 * min(drift, 5.0))))
            el = time.perf_counter() - t0
            print(f"  {d}  {written - prev_total:>11,} rows  {written / 1e6:6.1f}M total  "
                  f"{size / 1e9:5.2f} GB  {el:6.0f}s  "
                  f"({written / max(el, 1e-9) / 1e3:.0f}k rows/s)")

    size_gb = os.path.getsize(args.out) / 1e9
    print(f"\nDone: {args.out}  {written:,} rows  {size_gb:.2f} GB  "
          f"{time.perf_counter() - t0:.0f}s")
    return 0


def verify(path: str) -> int:
    import csv
    n = bad = unsorted = 0
    prev_ts = -1
    with open(path, newline="", encoding="ascii") as f:
        for row in csv.DictReader(f):
            n += 1
            try:
                ts = int(row["ts_us"])
                match = int(row["match_price"])
                bid = int(row["bid_price"])
                ask = int(row["ask_price"])
                ref = int(row["ref_price"])
                vol = int(row["match_volume"])
            except (KeyError, ValueError):
                bad += 1
                continue
            if ts < prev_ts:
                unsorted += 1
            prev_ts = ts
            if bid >= ask or bid > match or ask <= match:
                bad += 1
            if row["exchange"] == "HOSE":
                if match % tick_size(match):
                    bad += 1
                if vol % 100:
                    bad += 1
                if not (ref * 0.93 <= match <= ref * 1.07):
                    bad += 1
            if n % 10_000_000 == 0:
                print(f"  ... {n:,} rows")
    print(f"{path}: {n:,} rows, {bad:,} constraint violations, {unsorted:,} out-of-order")
    return 0 if bad == 0 and unsorted == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

