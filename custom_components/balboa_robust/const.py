"""Constants for the Balboa Robust integration."""

DOMAIN = "balboa_robust"

CONF_CONNECT_TIMEOUT = "connect_timeout"
CONF_BACKOFF_INITIAL = "backoff_initial"
CONF_BACKOFF_MAX = "backoff_max"
CONF_BACKOFF_FACTOR = "backoff_factor"
CONF_HEARTBEAT_INTERVAL = "heartbeat_interval"
CONF_STALE_AFTER = "stale_after"
CONF_MAX_RETRIES = "max_retries"
CONF_RECONNECT_ON_ERROR = "reconnect_on_error"
CONF_AUTO_PAUSE_AFTER = "auto_pause_after_failures"
CONF_STABLE_FOR = "stable_for"
CONF_UPTIME_WINDOW = "uptime_window"

DEFAULTS = {
    CONF_CONNECT_TIMEOUT: 5.0,
    CONF_BACKOFF_INITIAL: 5.0,
    CONF_BACKOFF_MAX: 120.0,
    CONF_BACKOFF_FACTOR: 2.0,
    CONF_HEARTBEAT_INTERVAL: 10.0,
    CONF_STALE_AFTER: 30.0,
    CONF_MAX_RETRIES: 0,
    CONF_RECONNECT_ON_ERROR: True,
    CONF_AUTO_PAUSE_AFTER: 0,
    CONF_STABLE_FOR: 10.0,
    CONF_UPTIME_WINDOW: 3600.0,
}

SERVICE_PAUSE = "pause"
SERVICE_RESUME = "resume"
SERVICE_SYNC_SPA_CLOCK = "sync_spa_clock"

DEFAULT_PORT = 4257
