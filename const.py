"""Constants for Voice Assistant HA."""

DOMAIN = "voice_assistant_ha"

CONF_URL = "url"
CONF_TOKEN = "token"
CONF_LABEL = "label"
CONF_ASSISTANT_NAME = "assistant_name"
CONF_MIN_CONFIDENCE = "min_confidence"
CONF_FALLBACK_AGENT = "fallback_agent"
CONF_DEVICES = "devices"
CONF_TG_URL = "telegram_url"
CONF_TG_TOKEN = "telegram_token"
CONF_TG_USERS = "telegram_users"

DEFAULT_URL = "http://192.168.88.100:8300"
DEFAULT_LABEL = "Голос"
DEFAULT_ASSISTANT_NAME = "Асистент"
DEFAULT_MIN_CONFIDENCE = 0.6
DEFAULT_FALLBACK_AGENT = "conversation.home_assistant"
NO_FALLBACK = "none"

# How long a question asked by the assistant waits for the answer.
PENDING_TTL_S = 120
REQUEST_TIMEOUT_S = 30
