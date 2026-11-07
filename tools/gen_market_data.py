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
  python gen_market_data.py 1GB                   # positional target size too
  python gen_market_data.py 1GB --sessions 5      # how far back to spread it
  python gen_market_data.py 6B                    # 6 bytes (tiny smoke test)
  python gen_market_data.py 80KB --out small.csv
  python gen_market_data.py 100MB --dirty 0.4 --seed-file .rows_seed
  python gen_market_data.py --verify out.csv      # sanity-check a file

Size suffixes: B, KB, MB, GB, TB (decimal, 1KB = 1000 bytes; also KiB/MiB/
GiB/TiB for powers of two). A bare number is gigabytes, as before.

Dirty rows: a share of the rows are pushed into the structural CSV cases a
hand-written parser must survive (comma inside a quoted field, ""-escaped
quotes, empty fields at the row boundaries). The extra columns
buyer_ref,seller_ref,venue_note are added to the header for them; clean rows
are padded with empty values. See gen_dirty.py for the full rule set.

--verify checks a file structurally (every line parses under those rules);
rows that carry no quoting are additionally checked against the market
invariants (bid < ask, tick alignment, price band).
"""

from __future__ import annotations

import argparse
import bisect
import math
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_dirty  # noqa: E402  (sibling module, same directory)

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
                day_mult: float, cap: int | None = None
                ) -> tuple[str, int]:
    """Simulate one session (morning or afternoon) for every ticker.

    Returns (text_body, n_rows). `nominal` is the requested base arrival
    count; emitted rows are ~nominal * uplift. `cap` limits the rows emitted
    per ticker: a ticker stops once it has produced `cap` rows of its own, so
    every row that could appear among the first `cap` of the merged, sorted
    stream is still generated (used by gen_market_data_lines.py to stop early
    when only the first N rows are wanted). The first cap rows of the sorted
    output are identical to an uncapped run's first cap rows.
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
        emitted = 0
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
            emitted += 1

            if st.burst_arr == 0 and rand() < BURST_P:
                st.burst_arr = BURST_LEN + int(rand() * BURST_LEN)

            if nxt <= t:
                nxt = t + 1
            t = nxt

            if cap is not None and emitted >= cap:
                # Stop only AFTER the full iteration has drawn its randoms,
                # so every later ticker's stream is unchanged and the first
                # `cap` sorted rows match an uncapped run exactly.
                break

        st.last_close = st.px

    rows.sort()
    if cap is not None and len(rows) > cap:
        del rows[cap:]                     # head is prefix-exact by construction
    return "\n".join(r[2] for r in rows), len(rows)


# ---------------------------------------------------------------------------
# Size arguments & dirty rendering (shared with gen_market_data_lines.py)
# ---------------------------------------------------------------------------

_SIZE_UNITS = {
    "": 1e9, "b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12,
    "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4,
}
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-z]*)\s*$", re.I)


def parse_size(text: str) -> int:
    """'1GB' / '6B' / '80KB' / '100MB' / '1.5GiB' -> bytes. Bare = gigabytes."""
    m = _SIZE_RE.match(str(text))
    if not m:
        raise argparse.ArgumentTypeError(
            "bad size %r (examples: 6B, 80KB, 100MB, 1GB, 1.5GiB)" % text)
    unit = m.group(2).lower()
    if unit not in _SIZE_UNITS:
        raise argparse.ArgumentTypeError("unknown size unit %r in %r"
                                         % (m.group(2), text))
    return int(float(m.group(1)) * _SIZE_UNITS[unit])


def fmt_bytes(n: float) -> str:
    for unit, div in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= div:
            return "%.2f %s" % (n / div, unit)
    return "%d B" % n


def load_rows_seed(path: str) -> int:
    """Load a pinned prints/day (see --seed-file) so runs share one density.

    Without a pin, every size calibrates its own prints/day, so two runs with
    different targets produce different row densities. With a pin, files of
    different sizes share the same rows/day (and, with the same --seed, the
    same per-day content).
    """
    if path and os.path.exists(path):
        try:
            with open(path, encoding="ascii") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            pass
    return 0


def save_rows_seed(path: str, rows_per_day: int) -> None:
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="ascii") as f:
            f.write("%d\n" % rows_per_day)
    except OSError:
        pass


def sweep_quoteless(line: str, core: list[str]) -> bool:
    """Market invariants for a row with no quoting; True if the row is good.

    Skipped for dirty rows: their numeric columns may hold vendor
    annotations, so the invariants only apply to clean rows.
    """
    if '"' in line:
        return True
    try:
        px, vol = int(core[5]), int(core[6])
        bid, ask = int(core[8]), int(core[9])
    except (ValueError, IndexError):
        return False
    if not (bid < ask and bid <= px < ask and vol > 0):
        return False
    if core[2] == "HOSE":
        if px % tick_size(px) or vol % 100:
            return False
    return True


def verify(path: str) -> int:
    """Structural + market-invariant check of `path`, including ts ordering."""
    bad, unsorted = gen_dirty.verify(path, core_check=sweep_quoteless,
                                     check_sorted=True)
    if unsorted:
        print(f"  {unsorted:,} rows out of timestamp order")
    return 0 if bad == 0 and unsorted == 0 else 1


def render_dirty(body: str, rng: random.Random, p: float) -> str:
    """Re-emit a batch of clean rows in place, dirtying roughly p of them.

    Walking the body row by row (instead of touching the hot inner loop)
    keeps generation speed the same as before for p = 0. Rows are processed
    in sorted output order and `rng` is advanced only for rows that are
    emitted, so truncating a batch at row k leaves the stream exactly where
    the full batch would have it: short files are prefixes of long ones.
    """
    if p <= 0.0:
        return body
    out = []
    for row in body.split("\n"):
        if rng.random() < p:
            out.append(gen_dirty.make_line(rng, row, 1.0)[0])
        else:
            out.append(gen_dirty.make_clean(row))
    return "\n".join(out)


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
    ap.add_argument("target", nargs="?", metavar="SIZE",
                    help="file size: 6B / 80KB / 100MB / 1GB (bare number = GB)")
    ap.add_argument("--out", default=None,
                    help="output path (default: market_ticks_<SIZE>.csv)")
    ap.add_argument("--target-gb", type=float, default=None,
                    help="file size in GB (legacy form of SIZE)")
    ap.add_argument("--sessions", type=int, default=None,
                    help="trading days to cover (default: 30, or as few as "
                         "needed for small sizes)")
    ap.add_argument("--rows-per-day", type=int, default=0,
                    help="override the auto-calibrated nominal prints/day")
    ap.add_argument("--seed-file", default="",
                    help="pin the calibrated prints/day in this file so runs "
                         "with different SIZE targets share one row density "
                         "(e.g. .rows_per_day; '' disables pinning)")
    ap.add_argument("--dirty", type=float, default=0.5,
                    help="fraction of rows emitted with CSV structural "
                         "complexity (quotes, escapes, empty fields); 0=off")
    ap.add_argument("--start", default="2026-08-03")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-retarget", action="store_true",
                    help="sample at a constant prints/day instead of "
                         "closed-loop retargeting per day; constant density is "
                         "what keeps the first N rows identical across files "
                         "(use together with --seed-file for exact prefixes)")
    ap.add_argument("--verify", metavar="CSV", default=None,
                    help="verify an existing file instead of generating")
    args = ap.parse_args()

    if args.verify:
        return verify(args.verify)

    if not 0.0 <= args.dirty <= 1.0:
        ap.error("--dirty must be between 0 and 1")

    # ---- resolve target size ----
    if args.target is not None:
        try:
            target_bytes = parse_size(args.target)
        except argparse.ArgumentTypeError as e:
            ap.error(str(e))
        label = args.target.upper().replace(" ", "")
    elif args.target_gb is not None:
        target_bytes = int(args.target_gb * 1e9)
        label = "%gGB" % args.target_gb
    else:
        target_bytes = int(5e9)
        label = "5GB"
    if target_bytes <= 0:
        ap.error("target size must be positive")
    out = args.out or "market_ticks_%s.csv" % label

    start = date.fromisoformat(args.start)
    # ~140 B/row; never stretch a tiny file over 30 days (and a KB-sized file
    # may only fit a handful of rows -- that is fine, it is a smoke test).
    if args.sessions is not None:
        sessions = max(1, args.sessions)
    else:
        days_needed = int(target_bytes / 140.0 / 2_000) + 1
        # No point asking for more days than there are rows to fill them.
        sessions = max(1, min(30, days_needed,
                              max(1, int(target_bytes / 140.0))))
    days = session_dates(start, sessions)
    t0 = time.perf_counter()

    pinned = 0 if args.rows_per_day > 0 else load_rows_seed(args.seed_file)
    if args.rows_per_day <= 0 and pinned > 0:
        args.rows_per_day = pinned
        print(f"  using pinned {pinned:,} prints/day from {args.seed_file}")

    if args.rows_per_day > 0:
        calib, bpr = measure(args.seed, 50_000, session_dates(start, 2))
    else:
        # Probe at production-scale density, then solve for the rows/day that
        # lands on the size target. Per-row bytes grow slightly when rows are
        # dirty (the extra vendor columns), so inflate the budget accordingly.
        probe = session_dates(start, 4)
        guess = int(target_bytes / 120.0 / max(sessions, 1))
        calib, bpr = measure(args.seed, max(guess, 1), probe)
        if bpr <= 0:                       # probe too small to measure B/row
            calib, bpr = 1.0, 130.0
        else:
            bpr *= 1.0 + 0.22 * args.dirty
        args.rows_per_day = max(
            1, int(target_bytes / bpr / sessions / calib))
        save_rows_seed(args.seed_file, args.rows_per_day)
        print(f"  calibration: uplift {calib:.3f}, {bpr:.1f} B/row -> "
              f"{args.rows_per_day:,} nominal prints/day"
              + (f" (pinned to {args.seed_file})" if args.seed_file else ""))

    print(f"Generating {out}: {label} target ({fmt_bytes(target_bytes)}), "
          f"{len(days)} sessions, {args.rows_per_day:,} nominal prints/day, "
          f"dirty {args.dirty:.0%}, start {args.start}, seed {args.seed}")

    rnd = random.Random(args.seed)
    dirty_rng = random.Random(args.seed ^ 0x5EED)
    states = [TickerState(inst, k) for k, inst in enumerate(INSTRUMENTS)]
    written = 0
    truncated = False
    target_per_day = args.rows_per_day

    with open(out, "w", newline="", encoding="ascii", buffering=1 << 23) as f:
        f.write((gen_dirty.DIRTY_HEADER if args.dirty > 0
                 else gen_dirty.CLEAN_HEADER) + "\n")
        size = f.tell()
        if size >= target_bytes:               # target smaller than the header
            print(f"  note: header alone is {size} B, target {target_bytes} B")
        for di, d in enumerate(days):
            if truncated or size >= target_bytes:
                break
            day_mult = math.exp(0.12 * rnd.gauss(0, 1))   # one per day, both sessions
            prev_total, day_bytes = written, 0
            for afternoon in (False, True):
                share = (MASS_A if afternoon else MASS_M) / (MASS_M + MASS_A)
                text, n = gen_session(d, afternoon, target_per_day * share,
                                      rnd, states, day_mult)
                text = render_dirty(text, dirty_rng, args.dirty)
                chunk = text + "\n"
                # Small targets: never overshoot, cut at a line boundary.
                room = target_bytes - (size + day_bytes)
                if len(chunk) > room:
                    keep = chunk[:max(0, room)].rfind("\n")
                    if keep > 0:
                        chunk = chunk[:keep + 1]
                        n = chunk.count("\n")
                        truncated = True
                    else:
                        n = 0
                    if not chunk:
                        truncated = True
                        break
                f.write(chunk)
                day_bytes += len(chunk)
                written += n
                if truncated:
                    break
            size += day_bytes
            # Closed-loop retarget: correct half the observed drift on what is
            # left, so the final size lands within a few percent of target.
            if di < len(days) - 1 and written and target_bytes > 0 \
                    and not args.no_retarget:
                per_row = size / written
                projected = size + (len(days) - di - 1) * per_row * target_per_day * calib
                drift = target_bytes / max(projected, 1) - 1.0
                target_per_day = max(1, int(target_per_day * (1.0 + 0.5 * min(drift, 5.0))))
            el = time.perf_counter() - t0
            print(f"  {d}  {written - prev_total:>11,} rows  {written / 1e6:6.1f}M total  "
                  f"{size / 1e9:5.2f} GB  {el:6.0f}s  "
                  f"({written / max(el, 1e-9) / 1e3:.0f}k rows/s)")

    size_b = os.path.getsize(out)
    print(f"\nDone: {out}  {written:,} rows  {fmt_bytes(size_b)} "
          f"({size_b / max(target_bytes, 1):.1%} of target)  "
          f"{time.perf_counter() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

