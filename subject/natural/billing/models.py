"""Core data shapes and constants for the billing engine."""

DEFAULT_CURRENCY = "usd"
# Number of decimal places in each currency's minor unit (cents for usd, none for jpy).
CURRENCY_EXPONENTS = {"usd": 2, "eur": 2, "gbp": 2, "jpy": 0, "kwd": 3}
CURRENCY_SYMBOLS = {"usd": "$", "eur": "€", "gbp": "£", "jpy": "¥"}
SECONDS_PER_DAY = 86_400
# Rates and percentages are stored in basis points: 10_000 bp == 100%.
BASIS_POINTS_SCALE = 10_000
# Days between finalizing an invoice and its due date.
INVOICE_NET_DAYS = 7
