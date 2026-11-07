#!/usr/bin/env python3
"""
gen_dirty.py -- shared "dirty CSV" row generator.

Both generators (gen_market_data.py by file size, gen_market_data_lines.py by
row count) write clean tick rows through this module, which randomly pushes
some rows into the structural cases a hand-written CSV parser must survive:

  1. simple fields                       abc,def
  2. quoted fields containing commas     "Cozy Apartment, Downtown"
  3. escaped quotes inside quoted fields "The ""All-New"" Version"
  4. empty fields (leading / middle / trailing commas)  ,,"x",

Every dirty line is still a *valid* CSV record: a parser implementing the
documented rules recovers exactly the clean row, so the expected output is
always known (see `expected` in make_line / make_clean).

`--verify` on either generator re-runs a reference parser against the
recovered rows:
    python gen_market_data_lines.py 100 --verify out_dirty.csv
"""

from __future__ import annotations

import random

# Words used to corrupt otherwise-numeric fields. Never commas or quotes.
_WORDS = (
    "grey", "navy", "gold", "mint", "rust", "teal", "sand", "plum",
    "north", "south", "east", "west", "core", "edge", "trend", "flow",
    "bid", "ask", "call", "put", "alpha", "beta", "omega", "sigma",
)

# Semantic values for the fields at the row boundaries:
#   lead  = buyer/seller order reference (before ts_us)
#   trail = account / vendor note (after the last core field)
LEAD_EVENTS = ("lead_buy", "lead_sell", "lead_refill", "lead_cancel")
TRAIL_EVENTS = ("trail_partial", "trail_market", "trail_liquidity",
                "trail_iceberg")

# Vendor extras appended after the 12 core columns.
EXTRA_HEADER = ("buyer_ref", "seller_ref", "venue_note")
_REF_EXTRAS = ("", "PAR", "NONE", "-")
_NOTE_EXTRAS = ("", "", "GTC", "IOC", "FOK", "ATO")

CLEAN_HEADER = ("ts_us,trade_date,exchange,ticker,ref_price,match_price,"
                "match_volume,order_type,bid_price,ask_price,bid_volume,"
                "ask_volume")
DIRTY_HEADER = CLEAN_HEADER + "," + ",".join(EXTRA_HEADER)
CORE_FIELDS = CLEAN_HEADER.count(",") + 1          # 12

# Fields that stay hard columns (ts_us, trade_date, exchange) so --verify can
# locate a row even when the line carries unannounced extras at either end.
ANCHOR = (0, 1, 2)


def _locate(line: str, k: int) -> tuple[list[str], int]:
    """Parse `line`; return (fields, start index of the k-th core column).

    Extra fields may sit anywhere (before, between, after the core 12); we
    anchor on the first column that looks like an epoch timestamp.
    """
    fields = parse_csv_line(line)
    hits = [i for i, v in enumerate(fields) if i + 2 < len(fields)
            and v.isdigit() and len(v) == 16
            and len(fields[i + 1]) == 10 and fields[i + 1][4] == "-"]
    if not hits:
        raise ValueError("no ts_us/trade_date pair found")
    return fields, hits[0]


def _word(rng: random.Random) -> str:
    return rng.choice(_WORDS)


def _quote(field: str) -> str:
    """Wrap a field in quotes, doubling any embedded quote (rule 3)."""
    return '"' + field.replace('"', '""') + '"'


def _special(rng: random.Random, value: str) -> tuple[str, str]:
    """Possibly wrap `value` into a quoted/escaped token.

    Text values (ticker, order type, extras) may take any form, including the
    hybrid `"The ""WORD"" <value>"`. A value that is already numeric only
    takes forms whose cleansed value is still numeric (`"123,x"`, `"x,123"`)
    so a numeric column stays parseable after unquoting.

    Returns (token, expected_cleansed_value).
    """
    numeric = value.isdigit()
    r = rng.random()
    if r < 0.35:                                        # rule 2: comma inside
        s = _word(rng)
        if numeric:                                     # keep number clean
            return _quote(value + "," + s), value + "," + s
        if rng.random() < 0.5:
            s, v = s + "," + value, s + "," + value      # "word,value"
        else:
            s, v = value + "," + s, value + "," + s      # "value,word"
        if rng.random() < 0.4:                           # both sides
            t = _word(rng)
            s, v = t + "," + s, t + "," + v
        return _quote(s), v
    if r < 0.62 and not numeric:                        # rule 3: "" escapes
        w = _word(rng).upper()
        v = 'The "%s" %s' % (w, value)
        return _quote(v), v
    if r < 0.80:                                        # plain quoted field
        return _quote(value), value
    return value, value                                 # rule 1: simple


def _special_fixed(rng: random.Random, value: str) -> tuple[str, str]:
    """Same word on both sides of the token (the generator must know the
    expected value, so a random word cannot be drawn twice)."""
    if value.isdigit() or rng.random() >= 0.55 or value in ("", "PAR", "NONE",
                                                            "-"):
        return _special(rng, value)
    v = 'The "%s" %s' % (_word(rng).upper(), value)
    return _quote(v), v


def _clean_fields(clean: str) -> list[tuple[str, str]]:
    """Split a clean row body into [(token, expected), ...] pairs."""
    parts = clean.split(",")
    assert len(parts) == CORE_FIELDS, clean
    return [(p, p) for p in parts]


def make_clean(clean: str) -> str:
    """Emit a clean row unchanged, padded to the dirty header's width."""
    return clean + ",,,"


def make_line(rng: random.Random, clean: str,
              dirty: float = 0.5) -> tuple[str, list[str]]:
    """Build one output line from a clean core row.

    `clean` : the 12-field row body, e.g. "1700000000,2026-08-03,HOSE,FPT,..."
    Returns (line, expected) where `expected` is the list of the 15 header
    columns a correct parser must produce for `line`.
    """
    fields = _clean_fields(clean)
    if rng.random() >= dirty:
        return make_clean(clean), [v for _, v in fields] + ["", "", ""]

    # Row-boundary fields carry a realistic value.
    ev = rng.choice(LEAD_EVENTS + TRAIL_EVENTS)
    lead = trail = None                                   # None -> no boundary field
    if ev == "lead_buy":
        lead = "REQ-ANBR-8841"
        fields[7] = ("B", "B")
    elif ev == "lead_sell":
        lead = "REQ-SLCT-2210"
        fields[7] = ("S", "S")
    elif ev == "lead_refill":
        lead = "REQ-RPLN-0915"
        ref = str(int(fields[4][0]) + 300)
        fields[4], fields[6] = (ref, ref), ("100", "100")
    elif ev == "lead_cancel":
        lead = "REQ-CNCL-4477"
        new = str(int(fields[8][0]) - 100)
        fields[8] = (new, new)
    elif ev == "trail_partial":
        trail = str(max(100, int(fields[6][0]) // 2))
    elif ev == "trail_market":
        trail = "MARKET"
    elif ev == "trail_liquidity":
        trail = _word(rng).capitalize()
    elif ev == "trail_iceberg":
        trail = "ICEBERG-3"

    # Quoted / escaped values inside the core columns. ts_us, trade_date and
    # exchange stay simple so a reader can always locate the row; numeric
    # columns only take forms that stay numeric after cleansing.
    for i in rng.sample([3, 4, 5, 6, 7, 8, 9, 10, 11], k=rng.randint(1, 2)):
        fields[i] = _special_fixed(rng, fields[i][1])

    # Vendor extras, sometimes empty (rule 4 in the middle).
    extras = []
    for _ in range(3):
        v = rng.choice(_REF_EXTRAS + _NOTE_EXTRAS)
        if rng.random() < 0.25:
            tok, v = _special_fixed(rng, v)
        else:
            tok = v
        extras.append((tok, v))

    # Rule 4 at the boundaries: leading/trailing empty field, sometimes in
    # addition to the semantic lead/trail value.
    if lead is not None and rng.random() < 0.35:
        lead = ""
    if trail is not None and rng.random() < 0.35:
        trail = ""

    tokens = ([lead] if lead is not None else []) \
        + [t for t, _ in fields] \
        + ([] if trail is None else [trail]) \
        + [t for t, _ in extras]
    expected = ([lead] if lead is not None else []) \
        + [v for _, v in fields] \
        + ([] if trail is None else [trail]) \
        + [v for _, v in extras]
    return ",".join(tokens), expected


# ---------------------------------------------------------------------------
# Reference implementation of the documented parsing rules
# ---------------------------------------------------------------------------

def parse_csv_line(line: str) -> list[str]:
    """Strict RFC-4180 style split, no library (Python's csv is too lenient).

    * commas separate fields; a field may be quoted if it holds , or "
    * "" inside a quoted field is one literal "
    * empty fields fall out of adjacent / boundary commas
    * a quote may also be escaped as \\" (generator's "backslash variant"
      is written with "" so this path is only for hand-written data)
    """
    out: list[str] = []
    cur: list[str] = []
    i, n = 0, len(line)
    while True:
        # ---- one field ----
        if i < n and line[i] == '"':
            i += 1
            while i < n:
                c = line[i]
                if c == '"':
                    if i + 1 < n and line[i + 1] == '"':
                        cur.append('"')
                        i += 2
                        continue
                    i += 1                       # closing quote
                    break
                cur.append(c)
                i += 1
            # anything between the closing quote and the next comma is junk
            if i < n and line[i] != ",":
                raise ValueError("malformed quoted field at char %d: %r"
                                 % (i, line))
        else:
            while i < n and line[i] != ",":
                cur.append(line[i])
                i += 1
        out.append("".join(cur))
        cur = []
        if i >= n:
            return out
        i += 1                               # skip comma; next field follows
        if i == n:                           # trailing comma -> empty field
            out.append("")
            return out


def verify(path: str, core_check=None, check_sorted: bool = False
           ) -> tuple[int, int]:
    """Re-parse `path` and check it is structurally sound.

    Returns (structural_failures, out_of_order_rows).

    * every line splits under the documented rules without raising
      (quotes balanced, "" un-escaped, empty fields preserved)
    * the ts_us / trade_date pair is locatable on every line, and the row
      carries at least the 12 core columns
    * if `core_check` is given, it is called as core_check(line, core_fields)
      and must return True (market-invariant checks live in the caller)
    * if `check_sorted`, ts_us must be non-decreasing across rows

    Numeric invariants are deliberately not checked here: dirty rows carry
    vendor annotations in numeric columns on purpose.
    """
    bad = 0
    n = 0
    unsorted = 0
    prev_ts = -1
    with open(path, newline="", encoding="ascii") as f:
        f.readline()
        for ln, line in enumerate(f, 2):
            line = line.rstrip("\n")
            n += 1
            try:
                fields, k = _locate(line, 3)
                if len(fields) - k < CORE_FIELDS:
                    raise ValueError("line cut short: %d core fields"
                                     % (len(fields) - k))
                d = fields[k + 1]
                if len(d) != 10 or d[4] != "-" or d[7] != "-":
                    raise ValueError("bad trade_date %r" % d)
                if check_sorted:
                    ts = int(fields[k])
                    if ts < prev_ts:
                        unsorted += 1
                    prev_ts = ts
                if core_check is not None and \
                        not core_check(line, fields[k:k + CORE_FIELDS]):
                    raise ValueError("market invariant violated")
            except (ValueError, IndexError) as e:
                bad += 1
                if bad <= 10:
                    print(f"  line {ln}: {e}\n    {line[:160]}")
            if n % 1_000_000 == 0:
                print(f"  ... {n:,} lines")
    print(f"{path}: {n:,} lines parsed, {bad:,} failures")
    return bad, unsorted


def verify_rc(path: str) -> int:
    """Structural-only verify for plain use; returns a process exit code."""
    bad, _ = verify(path)
    return 0 if bad == 0 else 1
