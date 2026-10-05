"""
Number and change formatting. Pure functions - no Streamlit, no data access -
so the rules the design language fixes (explicit signs, no -0.00, four decimals
for FX, Indian grouping for INR) live in exactly one place.
"""


def signed_pct(pct):
    """Format a percentage change with an explicit sign; never show -0.00."""
    if round(pct, 2) == 0:
        return "+0.00%"
    return f"{pct:+.2f}%"


def format_inr(value, decimals=2):
    """Indian digit grouping: 1234567.89 -> 12,34,567.89"""
    whole, _, frac = f"{value:.{decimals}f}".partition(".")
    neg = whole.startswith("-")
    whole = whole.lstrip("-")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return ("-" if neg else "") + whole + ("." + frac if frac else "")


def format_price(ticker, value, decimals=2):
    """Format a price with currency where the ticker makes it unambiguous."""
    if ticker.endswith((".NS", ".BO")):
        return "₹" + format_inr(value, decimals)
    return f"{value:,.{decimals}f}"


def price_decimals(ticker):
    """FX quotes keep four decimals and trailing zeros (spec); everything else two."""
    return 4 if ticker.endswith("=X") else 2


def signed_price_change(ticker, value, decimals=2):
    """Signed absolute change with the instrument's currency/unit; never -0.00."""
    if round(value, decimals) == 0:
        return "+" + format_price(ticker, 0, decimals)
    sign = "-" if value < 0 else "+"
    return sign + format_price(ticker, abs(value), decimals)


def elapsed_label(days):
    """Calendar days elapsed; weeks+days once it crosses two weeks (spec)."""
    if days <= 14:
        return f"{days}d"
    weeks, rem = divmod(days, 7)
    return f"{weeks}w {rem}d" if rem else f"{weeks}w"
