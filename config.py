import os

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_GROUP_ID = int(os.environ.get('TELEGRAM_GROUP_ID', '0'))
TELEGRAM_CHANNEL_ID = int(os.environ.get('TELEGRAM_CHANNEL_ID', '0'))
MEDIA_STORAGE_CHANNEL_ID = int(os.environ.get('MEDIA_STORAGE_CHANNEL_ID', '-1004495812759'))
TELEGRAM_ADMIN_ID = int(os.environ.get('TELEGRAM_ADMIN_ID', '0'))
TEST_MODE_ADMIN_ONLY = os.environ.get('TEST_MODE_ADMIN_ONLY', 'true').lower() in (
    '1', 'true', 'yes', 'on'
)

ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
JWT_SECRET = os.environ.get('JWT_SECRET', '')

ADMIN_UPI_ID = os.environ.get('ADMIN_UPI_ID', '')
ADMIN_LTC_ADDRESS = os.environ.get('ADMIN_LTC_ADDRESS', '')

DEFAULT_FEE_PERCENT = 1.0
DATABASE_URL = os.environ.get('DATABASE_URL') or 'postgresql://vaultpay:vaultpay@127.0.0.1:5432/vaultpay'

# ─── AI Dispute Judge (OpenRouter) ─────────────────────────────────────────────
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
AI_JUDGE_MODEL = os.environ.get('AI_JUDGE_MODEL', 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free')
# Deals at/below this amount get fully auto-resolved by the AI when it's confident.
# Larger deals still get an AI verdict + reasoning, but require admin confirmation.
AI_AUTO_THRESHOLD = int(os.environ.get('AI_AUTO_THRESHOLD', '200'))
AI_AUTO_CONFIDENCE_MIN = int(os.environ.get('AI_AUTO_CONFIDENCE_MIN', '65'))

# ─── Agentic add-ons: identity risk check ──────────────────────────────────────
# Deals at/above this amount trigger a lightweight identity risk check on both parties
# (new-looking account, no username, etc). Flags the admin — never auto-blocks a deal.
RISK_CHECK_AMOUNT_THRESHOLD = int(os.environ.get('RISK_CHECK_AMOUNT_THRESHOLD', '2000'))
ABUSE_WARNING_LIMIT = int(os.environ.get('ABUSE_WARNING_LIMIT', '3'))
