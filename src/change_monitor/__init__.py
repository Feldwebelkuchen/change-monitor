"""change-monitor: watch values on web pages and alert when they change.

A YAML file defines *watches* (URL + CSS selector + condition) and
*notifiers* (Telegram, e-mail, webhook, stdout). Every run fetches each
watch, stores the observation in SQLite and sends an alert when the
configured condition is met. The first observation is a silent baseline.
"""

from .config import Condition, Config, ConfigError, NotifierSpec, Watch, load_config
from .monitor import RunReport, WatchResult, run_loop, run_once
from .store import Observation, Store

__version__ = "0.1.0"

__all__ = [
    "Condition",
    "Config",
    "ConfigError",
    "NotifierSpec",
    "Observation",
    "RunReport",
    "Store",
    "Watch",
    "WatchResult",
    "__version__",
    "load_config",
    "run_loop",
    "run_once",
]
