#!/usr/bin/env python3
"""
gen_market_data_lines.py -- synthetic HOSE-style tick CSV generator, sized by
LINE COUNT instead of file size.

Same market model, parameters and seed as gen_market_data.py, so for the
same --rows-per-day / --seed / --dirty the first N rows written here are the
same rows gen_market_data.py produces. For an exact prefix of a SIZE run,
first run:  python gen_market_data.py 100MB --seed-file .rows_per_day
then:       python gen_market_data_lines.py 100 --seed-file .rows_per_day

    python gen_market_data_lines.py 100          # 100 rows (+ header)
    python gen_market_data_lines.py 2             # 2 rows
    python gen_market_data_lines.py 10000000      # 10 M rows
    python gen_market_data_lines.py 1000 --dirty 0.7 --out dirty.csv
    python gen_market_data_lines.py 1000 --verify out_dirty_1000.csv

Dirty rows (--dirty, default 0.5) exercise every structural case a hand-written
CSV parser must handle; see gen_dirty.py. The output always has the full
15-column header (ts_us ... ask_volume,buyer_ref,seller_ref,venue_note) and
clean rows are padded with empty values, so every line has a defined result
for all 15 columns.

Stop conditions (whichever comes first):
  * the requested line count is reached (file cut at a line boundary), or
  * the session calendar ends (--sessions, default 30 days). Raise --sessions
    for very large counts; rows/day is fixed at --rows-per-day.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_dirty  # noqa: E402
import gen_market_data as md  # noqa: E402

DEFAULT_ROWS_PER_DAY = 2_000_000     # pinned starting density if no seed file
SEED_FILE = ".rows_per_day"


def parse_count(text: str) -> int:
    """'100' -> 100, '10k' -> 10000, '1.5M' -> 1500000, '2M+' accepted as 2M."""
    s = str(text).strip().lower().rstrip("+")
    mult = 1
    for suffix, m in (("k", 1_000), ("m", 1_000_000), ("b", 1_000_000_000)):
        if s.endswith(suffix):
            s, mult = s[:-1], m
            break
    try:
        n = int(float(s) * mult)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "bad line count %r (examples: 2, 100, 10k, 1.5M)" % text) from None
    if n <= 0 or n > 10_000_000_000:
        raise argparse.ArgumentTypeError(
            "line count must be between 1 and 10 billion, got %r" % text)
    return n


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate a synthetic HOSE-style tick CSV with an exact "
                    "number of rows.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("lines", type=parse_count, metavar="LINES",
                    help="number of data rows to write (header excluded); "
                         "suffixes k/M/B allowed")
    ap.add_argument("--out", default=None,
                    help="output path (default: market_ticks_lines_<LINES>.csv)")
    ap.add_argument("--rows-per-day", type=int, default=0,
                    help="nominal prints/day (default: pinned in --seed-file, "
                         "else %d)" % DEFAULT_ROWS_PER_DAY)
    ap.add_argument("--seed-file", default="",
                    help="read a prints/day pinned by gen_market_data.py "
                         "--seed-file so both tools share one density "
                         "(e.g. .rows_per_day; '' disables)")
    ap.add_argument("--sessions", type=int, default=30,
                    help="max trading days to spread the rows over")
    ap.add_argument("--dirty", type=float, default=0.5,
                    help="fraction of rows emitted with CSV structural "
                         "complexity; 0=off")
    ap.add_argument("--start", default="2026-08-03")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--verify", metavar="CSV", default=None,
                    help="verify an existing file instead of generating")
    args = ap.parse_args()

    if args.verify:
        return gen_dirty.verify_rc(args.verify)
    if not 0.0 <= args.dirty <= 1.0:
        ap.error("--dirty must be between 0 and 1")

    target = args.lines
    out = args.out or "market_ticks_lines_%d.csv" % target
    t0 = time.perf_counter()

    rows_per_day = args.rows_per_day or md.load_rows_seed(args.seed_file) \
        or DEFAULT_ROWS_PER_DAY
    days = md.session_dates(date.fromisoformat(args.start), max(1, args.sessions))

    print(f"Generating {out}: {target:,} rows, {len(days)} sessions max, "
          f"{rows_per_day:,} prints/day, dirty {args.dirty:.0%}, "
          f"start {args.start}, seed {args.seed}")

    rnd = random.Random(args.seed)
    dirty_rng = random.Random(args.seed ^ 0x5EED)
    states = [md.TickerState(inst, k) for k, inst in enumerate(md.INSTRUMENTS)]
    written = 0
    done = False
    # Cap rows per ticker per session at what can still matter: the first
    # `target` rows of the sorted stream need at most `target` rows from any
    # one ticker, so simulating more is wasted work (2 rows would otherwise
    # still simulate a full 2M-print session before being cut).
    cap = target

    with open(out, "w", newline="", encoding="ascii", buffering=1 << 23) as f:
        f.write((gen_dirty.DIRTY_HEADER if args.dirty > 0
                 else gen_dirty.CLEAN_HEADER) + "\n")
        for di, d in enumerate(days):
            if done:
                break
            day_mult = math.exp(0.12 * rnd.gauss(0, 1))
            prev = written
            for afternoon in (False, True):
                share = ((md.MASS_A if afternoon else md.MASS_M)
                         / (md.MASS_M + md.MASS_A))
                text, _ = md.gen_session(d, afternoon, rows_per_day * share,
                                         rnd, states, day_mult, cap=cap)
                text = md.render_dirty(text, dirty_rng, args.dirty)
                rows = text.split("\n") if text else []
                left = target - written
                if len(rows) > left:
                    rows = rows[:left]
                    done = True
                if rows:
                    f.write("\n".join(rows) + "\n")
                    written += len(rows)
                if done:
                    break
            el = time.perf_counter() - t0
            print(f"  {d}  {written - prev:>11,} rows  {written:>12,}/{target:,}  "
                  f"{el:6.0f}s")

    if written < target:
        print(f"\nWARNING: only {written:,} of {target:,} rows fit in "
              f"{len(days)} sessions (~{rows_per_day * len(days):,} rows max). "
              f"Re-run with a larger --sessions (e.g. "
              f"{max(1, -(-target // max(rows_per_day, 1)) + 1)}).",
              file=sys.stderr)
    else:
        print(f"\nDone: {out}  {written:,} rows  "
              f"{os.path.getsize(out) / 1e6:.2f} MB  "
              f"{time.perf_counter() - t0:.1f}s")
    return 0 if written == target else 1


if __name__ == "__main__":
    sys.exit(main())
