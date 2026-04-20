from enum import Enum

DOMAIN = "energy_filter"
CONF_SOURCE_ENTITY = "source_entity"
CONF_NAME = "name"
CONF_UNIT_OF_MEASUREMENT = "unit_of_measurement"

OPT_KEEP_AVAILABLE = "keep_available"
OPT_KEEP_LAST_VALUE = "keep_last_value"
OPT_EXPECTED_RESET_INTERVAL = "expected_reset_interval"
OPT_EXPECTED_OVERRUN_MINUTES = "expected_overrun_minutes"


class ResetInterval(Enum):
    NEVER = 0
    DAILY = 1
    WEEKLY = 2
    MONTHLY = 3


DEFAULT_KEEP_AVAILABLE = True
DEFAULT_KEEP_LAST_VALUE = True
DEFAULT_EXPECTED_RESET_INTERVAL = ResetInterval.NEVER.name
DEFAULT_EXPECTED_OVERRUN_MINUTES = 0
