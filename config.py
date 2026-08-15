# ─── config.py ────────────────────────────────────────────────────────────────

# Telegram Bot Token (from @BotFather)
BOT_TOKEN = "8803249989:AAHZ1sC-heMy7fHXajHUQXxw2lCGhefIwjc"

# Pyrogram API credentials (from my.telegram.org)
API_ID   = 38889389
API_HASH = "cd110cdb3698e72a8eff4ab15c927d3a"

# Owner Telegram User IDs (never removable as admin)
OWNER_IDS = [8515110962]

# SQLite database file paths
DB_FILE            = "bot_database.db"
CENTRAL_PREMIUM_DB = "premium_users.db"

# Number of content button slots
BUTTON_SLOTS = 10

# Rate limiting: max clicks per window (seconds), then freeze duration (seconds)
RATE_LIMIT_CLICKS = 10
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_FREEZE = 300

# How many free slot clicks before forcing verification
PHONE_REQUEST_THRESHOLD = 3

# Mini App URL (your Vercel/hosted index.html URL)
# The bot will show this as a WebApp button to users
MINI_APP_URL = "https://my-new-bot-iota.vercel.app/"

# Web API server settings (aiohttp)
# 0.0.0.0 = listen on all interfaces; change port if needed
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = 8080

# Default background image URL for the Mini App login page
# Admin can override this at runtime with /setbg command
# Use any direct image URL (jpg/png/webp)
LOGIN_BG_URL = "https://images.unsplash.com/photo-1614854262318-831574f15f1f?w=1200&q=80"

# Extra Pyrogram API credentials for rotation (optional)
# Format: [{"api_id": 123, "api_hash": "abc"}, ...]
EXTRA_API_CREDENTIALS = []
