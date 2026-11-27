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
CONF_VOLUME_ADAPT = "volume_adapt"
CONF_FOLLOW_UP = "follow_up"
CONF_HOME_AREAS = "home_areas"
CONF_RESET_HABITS = "reset_habits"
CONF_VOLUME_MIN = "volume_min"
CONF_VOLUME_MAX = "volume_max"
CONF_QUIET_DB = "quiet_db"
CONF_LOUD_DB = "loud_db"
CONF_TG_TOKEN = "telegram_token"
CONF_TG_USERS = "telegram_users"

DEFAULT_URL = "http://192.168.88.100:8300"
DEFAULT_LABEL = "Голос"
DEFAULT_ASSISTANT_NAME = "Асистент"
DEFAULT_MIN_CONFIDENCE = 0.6
DEFAULT_FALLBACK_AGENT = "conversation.home_assistant"
NO_FALLBACK = "none"
DEFAULT_VOLUME_ADAPT = True
DEFAULT_FOLLOW_UP = True
# What STT returns instead of nothing while the agent waits for an answer:
# the pipeline would stop with an error (and a beep) on empty text.
FOLLOW_UP_SILENCE = "<тиша>"
FOLLOW_UP_WINDOW_S = 30
DEFAULT_VOLUME_MIN = 0.3
DEFAULT_VOLUME_MAX = 1.0
DEFAULT_QUIET_DB = -45.0
DEFAULT_LOUD_DB = -20.0

# How long a question asked by the assistant waits for the answer.
PENDING_TTL_S = 120
REQUEST_TIMEOUT_S = 30
