"""Step 0 for per-symbol basis detection: does yfinance report a given
symbol on a CONSISTENT basis (standalone vs consolidated), and is that
basis the SAME for EPS and revenue?

Both questions have to hold for jobs/detect_basis.py to be sound:

  - Consistent ACROSS QUARTERS, because the whole point is to resolve a
    symbol's basis from completed history and apply it to a fresh result
    immediately. Yahoo lags - it had no row at all for ANANDRATHI's
    Sep-2026 quarter days after the print - so detecting from the current
    quarter is not an option.
  - Consistent ACROSS MEASURES, because if EPS resolves standalone while
    revenue resolves consolidated, one stored basis per symbol cannot
    serve both and the schema needs two.

Method: for each completed quarter yfinance reports, compare its figure
against BOTH NSE filings for that quarter (standalone and consolidated,
which NSE publishes as two separate XBRL documents). Record which one it
matches within tolerance. EPS compares diluted, since yfinance's reported
EPS matched the diluted figure in every discriminating case checked by
hand. Revenue compares the stored measure, which for a bank is the
derived NII+OtherIncome rather than the Income line - see
nse_results._derive_bank_revenue.

Read-only. Writes nothing but its own report.

Run: .venv/bin/python -m scripts.discover_yahoo_basis [--symbols A,B,C]
"""

import argparse
import sys
import time
from datetime import date, datetime

import yfinance as yf

import events_db as ed
import nse_results as nr
from jobs.fill_actuals import quarter_end_for_event_date

#: Relative tolerance for calling two figures "the same". EPS is quoted to
#: 2dp by both sides, so anything under half a paisa on a ~10-rupee figure
#: is rounding, not a different measure. Revenue differences between the
#: two bases are percentage-scale, never this small.
REL_TOL = 0.005
SLEEP = 1.5


def _close(a, b):
    if a is None or b is None:
        return False
    a, b = float(a), float(b)
    if a == 0 or b == 0:
        return abs(a - b) < 1e-9
    return abs(a - b) / max(abs(a), abs(b)) <= REL_TOL


def yahoo_eps_by_quarter(symbol):
    """{quarter_end: reported_eps} from yfinance's per-quarter history."""
    out = {}
    hist = ed.get_eps_history(symbol)
    if hist is None or hist.empty:
        return out
    for _, row in hist.iterrows():
        if not row["event_date"] or row["eps_actual"] is None:
            continue
        qe = quarter_end_for_event_date(date.fromisoformat(row["event_date"]))
        out[qe] = float(row["eps_actual"])
    return out


def yahoo_revenue_by_quarter(symbol):
    """{quarter_end: total_revenue} from the quarterly income statement."""
    out = {}
    try:
        stmt = yf.Ticker(f"{symbol}.NS").quarterly_income_stmt
    except Exception:
        return out
    if stmt is None or stmt.empty or "Total Revenue" not in stmt.index:
        return out
    for col in stmt.columns:
        qe = col.date() if hasattr(col, "date") else None
        value = stmt.loc["Total Revenue", col]
        if qe is None or value is None or value != value:
            continue
        out[qe] = float(value)
    return out


def classify(symbol, max_quarters=4):
    """Per-quarter verdicts for one symbol."""
    rows = nr.fetch_filing_index(symbol)
    y_eps = yahoo_eps_by_quarter(symbol)
    y_rev = yahoo_revenue_by_quarter(symbol)
    quarters = sorted(set(y_eps) | set(y_rev), reverse=True)[:max_quarters]

    verdicts = []
    for qe in quarters:
        facts = {}
        for basis in (nr.BASIS_CONSOLIDATED, nr.BASIS_STANDALONE):
            try:
                facts[basis] = nr.get_quarter_actuals(symbol, qe, basis, rows=rows)
            except Exception:
                facts[basis] = None
            time.sleep(SLEEP)
        cons, std = facts.get(nr.BASIS_CONSOLIDATED), facts.get(nr.BASIS_STANDALONE)
        if not cons and not std:
            continue

        def verdict(y_val, key):
            c = cons.get(key) if cons else None
            s = std.get(key) if std else None
            hits = []
            if _close(y_val, c):
                hits.append("cons")
            if _close(y_val, s):
                hits.append("std")
            if not hits:
                return "none", c, s
            if len(hits) == 2:
                return "both", c, s   # the two bases are equal - no information
            return hits[0], c, s

        eps_v, eps_c, eps_s = verdict(y_eps.get(qe), "eps_diluted")
        rev_v, rev_c, rev_s = verdict(y_rev.get(qe), "revenue")
        verdicts.append({
            "quarter_end": qe, "eps_verdict": eps_v, "rev_verdict": rev_v,
            "y_eps": y_eps.get(qe), "eps_cons": eps_c, "eps_std": eps_s,
            "y_rev": y_rev.get(qe), "rev_cons": rev_c, "rev_std": rev_s,
        })
    return verdicts


def summarise(verdicts, key):
    """A single basis for a symbol from its per-quarter verdicts, ignoring
    quarters that carry no information ("both" means the two bases are
    numerically equal that quarter)."""
    informative = [v[key] for v in verdicts if v[key] in ("cons", "std")]
    if not informative:
        return "unknown", 0, 0
    agree = max(set(informative), key=informative.count)
    return agree, informative.count(agree), len(informative)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", help="comma-separated; default is a mixed sample")
    ap.add_argument("--quarters", type=int, default=4)
    args = ap.parse_args()

    symbols = (args.symbols.split(",") if args.symbols else
               ["TCS", "HCLTECH", "RBLBANK", "RELIANCE", "HDFCBANK",
                "BAJFINANCE", "TATAELXSI", "INFY", "SBIN", "M&M"])

    print(f"# yfinance basis discovery, run {datetime.now().isoformat(timespec='seconds')}")
    print(f"# tolerance {REL_TOL:.1%}; 'both' = the two bases are equal that quarter (no signal)")
    print()
    eps_mismatch, measure_mismatch = [], []

    for symbol in symbols:
        try:
            verdicts = classify(symbol, args.quarters)
        except Exception as e:
            print(f"{symbol}: ERROR {type(e).__name__}: {e}")
            continue
        if not verdicts:
            print(f"{symbol}: no comparable quarters")
            continue

        eps_basis, eps_n, eps_tot = summarise(verdicts, "eps_verdict")
        rev_basis, rev_n, rev_tot = summarise(verdicts, "rev_verdict")
        consistent_eps = eps_tot and eps_n == eps_tot
        same_measure = (eps_basis == rev_basis) and eps_basis != "unknown" and rev_basis != "unknown"

        print(f"{symbol}: EPS={eps_basis} ({eps_n}/{eps_tot} quarters agree)  "
              f"REV={rev_basis} ({rev_n}/{rev_tot})  "
              f"{'EPS-CONSISTENT' if consistent_eps else 'EPS-INCONSISTENT'}  "
              f"{'SAME-BASIS' if same_measure else 'MEASURES-DIFFER'}")
        for v in verdicts:
            print(f"    {v['quarter_end']}  eps y={v['y_eps']} c={v['eps_cons']} s={v['eps_std']} -> {v['eps_verdict']}"
                  f"   | rev y={v['y_rev']} c={v['rev_cons']} s={v['rev_std']} -> {v['rev_verdict']}")
        if eps_tot and not consistent_eps:
            eps_mismatch.append(symbol)
        if eps_basis != "unknown" and rev_basis != "unknown" and eps_basis != rev_basis:
            measure_mismatch.append(symbol)

    print()
    print("# VERDICT")
    print(f"#   symbols whose EPS basis flips between quarters: {eps_mismatch or 'none'}")
    print(f"#   symbols whose EPS and revenue bases disagree:   {measure_mismatch or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
