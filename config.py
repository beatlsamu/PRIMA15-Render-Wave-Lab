import os

from dotenv import load_dotenv

# Load local secrets before reading any configuration variables. Render uses environment vars.
load_dotenv()

APP_VERSION = "4.5.0-elliott-prime-hybrid"

SYMBOL = os.getenv("SYMBOL", "RENDERUSDT").upper().replace("/", "")
INTERVAL = "15m"
INTERVAL_MS = 15 * 60 * 1000
HISTORY_CANDLES = max(300, min(int(os.getenv("HISTORY_CANDLES", "5000")), 5000))
POLL_SECONDS = max(5, int(os.getenv("POLL_SECONDS", "8")))
KLINE_REFRESH_SECONDS = max(10, int(os.getenv("KLINE_REFRESH_SECONDS", "18")))
FAST_MARKET_REFRESH_SECONDS = max(10, int(os.getenv("FAST_MARKET_REFRESH_SECONDS", "20")))
FAST_MARKET_HISTORY_CANDLES = max(180, min(int(os.getenv("FAST_MARKET_HISTORY_CANDLES", "3000")), 3000))
FAST_ENTRY_REFERENCE_CANDLES = max(600, min(int(os.getenv("FAST_ENTRY_REFERENCE_CANDLES", "2400")), FAST_MARKET_HISTORY_CANDLES))
FAST_ENTRY_MIN_DIRECTIONAL_EDGE_GAP_PCT = max(0.0, min(20.0, float(os.getenv("FAST_ENTRY_MIN_DIRECTIONAL_EDGE_GAP_PCT", "4.5"))))
FAST_ENTRY_ENFORCE_GATE = os.getenv("FAST_ENTRY_ENFORCE_GATE", "false").strip().lower() in {"1", "true", "yes", "on"}
FAST_SETUP_COOLDOWN_SECONDS = max(180, int(os.getenv("FAST_SETUP_COOLDOWN_SECONDS", "300")))
FAST_ENTRY_ADVERSE_PCT = max(0.05, min(1.0, float(os.getenv("FAST_ENTRY_ADVERSE_PCT", "0.12"))))
FAST_ENTRY_HORIZON_MINUTES = max(5, min(60, int(os.getenv("FAST_ENTRY_HORIZON_MINUTES", "20"))))
FAST_ENTRY_MIN_SAMPLES = max(10, min(100, int(os.getenv("FAST_ENTRY_MIN_SAMPLES", "25"))))
FAST_ENTRY_MIN_HIT_RATE_PCT = max(50.0, min(90.0, float(os.getenv("FAST_ENTRY_MIN_HIT_RATE_PCT", "62"))))
FAST_ENTRY_MIN_WILSON_PCT = max(35.0, min(80.0, float(os.getenv("FAST_ENTRY_MIN_WILSON_PCT", "50"))))
FAST_ENTRY_MIN_LIVE_CONFIRMATION_PCT = max(50.0, min(90.0, float(os.getenv("FAST_ENTRY_MIN_LIVE_CONFIRMATION_PCT", "58"))))
FAST_ENTRY_MAX_SPREAD_BPS = max(1.0, min(25.0, float(os.getenv("FAST_ENTRY_MAX_SPREAD_BPS", "8"))))
FAST_ENTRY_MAX_MINUTES_TO_TARGET = max(3.0, min(60.0, float(os.getenv("FAST_ENTRY_MAX_MINUTES_TO_TARGET", "15"))))
OPENROUTER_MAX_COMMITTEES_PER_DAY = max(1, min(40, int(os.getenv("OPENROUTER_MAX_COMMITTEES_PER_DAY", "20"))))
DATA_DIR = os.getenv("DATA_DIR", "data")
DB_PATH = os.path.join(DATA_DIR, "prime_wave.sqlite3")

# Binance public endpoints. No API key is needed for market data.
MARKET_PROVIDER = os.getenv("MARKET_PROVIDER", "auto").lower()
BINANCE_BASES = [
    os.getenv("BINANCE_BASE_URL", "https://api.binance.com").rstrip("/"),
    "https://api-gcp.binance.com",
    "https://data-api.binance.vision",
    "https://api1.binance.com",
    "https://api2.binance.com",
]
BYBIT_BASES = ["https://api.bybit.com", "https://api.bytick.com"]

# OpenRouter: two independent voters; the third model is a fallback if one fails.
# Fast continuous paper-cycle cadence. Migrate older defaults (1h/2h) to 5m
# so copying an old .env does not silently preserve the slow schedule.
_interval_raw = int(os.getenv("OPENROUTER_INTERVAL_SECONDS", "300"))
if _interval_raw in {3600, 7200}:
    _interval_raw = 300
OPENROUTER_INTERVAL_SECONDS = max(300, _interval_raw)
OPENROUTER_TIMEOUT_SECONDS = max(8, min(35, int(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "35"))))
# Migrate the old 62-point example confidence threshold to the new 30-point threshold.
_ai_confidence_raw = float(os.getenv("AI_MIN_CONFIDENCE", "30"))
if _ai_confidence_raw == 62.0:
    _ai_confidence_raw = 30.0
AI_MIN_CONFIDENCE = max(0.0, min(90.0, _ai_confidence_raw))
AI_REQUIRED_FOR_SIGNALS = os.getenv("AI_REQUIRED_FOR_SIGNALS", "true").strip().lower() in {"1", "true", "yes", "on"}

# Simulated wallet accounting is percentage-indexed (100.0 = starting balance).
SIM_OPEN_FEE_PCT = max(0.0, min(1.0, float(os.getenv("SIM_OPEN_FEE_PCT", "0.035"))))
SIM_POSITION_SIZE_PCT = max(1.0, min(100.0, float(os.getenv("SIM_POSITION_SIZE_PCT", "100"))))
SIM_TAKE_PROFIT_PCT = max(0.05, min(5.0, float(os.getenv("SIM_TAKE_PROFIT_PCT", "0.14"))))
