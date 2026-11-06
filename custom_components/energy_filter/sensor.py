from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    async_get_current_platform,
)
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import ExtraStoredData
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SOURCE_ENTITY,
    DEFAULT_EXPECTED_OVERRUN_MINUTES,
    DEFAULT_EXPECTED_RESET_INTERVAL,
    DEFAULT_KEEP_AVAILABLE,
    DEFAULT_KEEP_LAST_VALUE,
    OPT_EXPECTED_OVERRUN_MINUTES,
    OPT_EXPECTED_RESET_INTERVAL,
    OPT_KEEP_AVAILABLE,
    OPT_KEEP_LAST_VALUE,
    ResetInterval,
)

_LOGGER = logging.getLogger(__name__)


def _get_source_name(hass: HomeAssistant, entry: er.RegistryEntry) -> str:
    """Return the entity name with the device name prefix stripped, if applicable."""
    # entity_registry.async_get_unprefixed_name was removed in HA 2026.11,
    # the equivalent is now the RegistryEntry.entity_name property.
    if hasattr(entry, "entity_name"):
        return entry.entity_name
    return er.async_get_unprefixed_name(hass, entry)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    source_entity_id = entry.data[CONF_SOURCE_ENTITY]
    source_entry = ent_reg.async_get(source_entity_id)
    if source_entry:
        source_name = _get_source_name(hass, source_entry)
        source_unit_of_measurement = source_entry.unit_of_measurement
        device_info = None
        device_id = source_entry.device_id
        if device_id:
            device = dev_reg.async_get(device_id)
            if device:
                device_info = DeviceInfo(
                    identifiers=device.identifiers,
                )

        async_add_entities(
            [
                EnergyFilterSensor(
                    entry, source_name, source_unit_of_measurement, device_info
                )
            ]
        )

    platform = async_get_current_platform()
    platform.async_register_entity_service("reset", {}, "async_reset")


@dataclass
class EnergyFilterExtraData(ExtraStoredData):
    """Keeps custom attributes to be restored."""

    last_value: float | None
    last_source_reset: datetime | None

    def as_dict(self) -> dict[str, Any]:

        last_source_reset = (
            self.last_source_reset.isoformat()
            if self.last_source_reset is not None
            else None
        )
        return {
            "last_value": self.last_value,
            "last_source_reset": last_source_reset,
        }

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> EnergyFilterExtraData | None:
        try:
            return cls(
                last_value=restored.get("last_value"),
                last_source_reset=dt_util.parse_datetime(
                    restored.get("last_source_reset", "")
                ),
            )
        except Exception:  # noqa: BLE001
            return None


class EnergyFilterSensor(RestoreSensor):
    def __init__(
        self,
        entry: ConfigEntry,
        source_name: str,
        source_unit_of_measurement: str | None,
        device_info: DeviceInfo | None,
    ) -> None:
        self._source_entity_id: str = entry.data[CONF_SOURCE_ENTITY]

        self._opt_keep_available: bool = entry.options.get(
            OPT_KEEP_AVAILABLE, DEFAULT_KEEP_AVAILABLE
        )
        self._opt_keep_last_value: bool = entry.options.get(
            OPT_KEEP_LAST_VALUE, DEFAULT_KEEP_LAST_VALUE
        )
        self._opt_expected_reset_interval: ResetInterval = ResetInterval[
            entry.options.get(
                OPT_EXPECTED_RESET_INTERVAL, DEFAULT_EXPECTED_RESET_INTERVAL
            )
        ]
        self._opt_expected_overrun_td: timedelta = timedelta(
            minutes=entry.options.get(
                OPT_EXPECTED_OVERRUN_MINUTES, DEFAULT_EXPECTED_OVERRUN_MINUTES
            )
        )

        entry_unique_id = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{entry_unique_id}_filtered"

        self._attr_has_entity_name = True
        self._attr_name = f"{source_name} Filtered"
        self._attr_device_info = device_info

        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = source_unit_of_measurement
        self._attr_icon = "mdi:lightning-bolt-circle"

        self._last_value: float | None = None
        self._last_source_reset: datetime | None = None

        self._source_available: bool = False
        self._is_valid: bool = False

        self._unsub_state_listener = None
        self._unsub_interval_listener = None

    def _get_value(self, state: State | None) -> tuple[bool, float | None]:
        if state is None:
            return False, None
        if state.state == STATE_UNAVAILABLE:
            return False, None
        if state.state == STATE_UNKNOWN:
            return True, None

        try:
            value = float(state.state)
        except ValueError:
            return True, None
        return True, value

    def _get_expected_reset_td(self, now: datetime) -> timedelta | None:
        if self._last_source_reset is None:
            return None

        match self._opt_expected_reset_interval:
            case ResetInterval.NEVER:
                return None
            case ResetInterval.DAILY:
                d1 = now.replace(hour=0, minute=0, second=0, microsecond=0)
                d2 = self._last_source_reset.replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                if d1 > d2:
                    td = now - d1
                    return td
            case ResetInterval.WEEKLY:
                d1 = now.replace(hour=0, minute=0, second=0, microsecond=0)
                d1 -= timedelta(days=d1.weekday())
                d2 = self._last_source_reset.replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                d2 -= timedelta(days=d2.weekday())
                if d1 > d2:
                    td = now - d1
                    return td
            case ResetInterval.MONTHLY:
                d1 = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                d2 = self._last_source_reset.replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                )
                if d1 > d2:
                    td = now - d1
                    return td

        return None

    async def async_added_to_hass(self) -> None:
        # last_state = await self.async_get_last_state()
        # if last_state and last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        #     try:
        #         self._last_value = float(last_state.state)
        #         # self._attr_native_value = self._last_value
        #     except ValueError:
        #         pass

        # Restore custom attributes
        extra_data = await self.async_get_last_extra_data()
        if extra_data:
            restored = EnergyFilterExtraData.from_dict(extra_data.as_dict())
            if restored:
                self._last_value = restored.last_value
                self._last_source_reset = restored.last_source_reset

        self._unsub_state_listener = async_track_state_change_event(
            self.hass, [self._source_entity_id], self._handle_source_state_change
        )
        self._unsub_interval_listener = async_track_time_interval(
            self.hass, self._handle_interval, timedelta(minutes=1)
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_state_listener:
            self._unsub_state_listener()
        if self._unsub_interval_listener:
            self._unsub_interval_listener()

    @property
    def extra_restore_state_data(self) -> EnergyFilterExtraData:
        return EnergyFilterExtraData(
            last_value=self._last_value,
            last_source_reset=self._last_source_reset,
        )

    @callback
    def _handle_source_state_change(self, event) -> None:
        event_dt = event.time_fired
        new_state = event.data.get("new_state")
        # old_state = event.data.get("old_state")

        self._source_available, new_value = self._get_value(new_state)
        if not self._source_available:
            self._is_valid = False
            self.async_write_ha_state()
            return

        if new_value is None:
            self._is_valid = False
            self.async_write_ha_state()
            return

        if self._last_value is None or self._last_source_reset is None:
            # initial sensor setup
            self._last_value = new_value
            self._last_source_reset = event_dt
            self._is_valid = True
            self.async_write_ha_state()
            return

        expected_reset_td = self._get_expected_reset_td(event_dt)
        if expected_reset_td is not None:
            if new_value == 0.0:
                self._last_value = 0.0
                self._last_source_reset = event_dt
                self._is_valid = True
                self.async_write_ha_state()
                return

            if expected_reset_td >= self._opt_expected_overrun_td:
                _LOGGER.info(
                    "%s: rejected value %.3f, waiting for reset",
                    self._source_entity_id,
                    new_value,
                )
                self._last_value = 0.0
                self._is_valid = False
                self.async_write_ha_state()
                return

        if new_value < self._last_value:
            _LOGGER.info(
                "%s: rejected value drop %.3f -> %.3f",
                self._source_entity_id,
                self._last_value,
                new_value,
            )
            self._is_valid = False
            self.async_write_ha_state()
            return

        # if old_state is not None and old_state.state not in (
        #     STATE_UNKNOWN,
        #     STATE_UNAVAILABLE,
        # ):
        #     try:
        #         old_value = float(old_state.state)
        #         delta = new_value - old_value
        #         elapsed_hours = (
        #             new_state.last_changed - old_state.last_changed
        #         ).total_seconds() / 3600
        #         if elapsed_hours > 0:
        #             rate = delta / elapsed_hours
        #             if rate > MAX_AVG_POWER_KW:
        #                 _LOGGER.info(
        #                     "%s: odbačen spike %.3f -> %.3f (%.3f kWh/h > %.3f limit)",
        #                     self._source_entity_id,
        #                     old_value,
        #                     new_value,
        #                     rate,
        #                     MAX_AVG_POWER_KW,
        #                 )
        #                 return
        #             _LOGGER.info(
        #                 "%s: prihvacen spike %.3f -> %.3f (%.3f kWh/h > %.3f limit)",
        #                 self._source_entity_id,
        #                 old_value,
        #                 new_value,
        #                 rate,
        #                 MAX_AVG_POWER_KW,
        #             )
        #     except ValueError, TypeError:
        #         pass

        self._last_value = new_value
        self._is_valid = True
        self.async_write_ha_state()

    async def async_reset(self) -> None:
        source_state = self.hass.states.get(self._source_entity_id)
        source_available, source_value = self._get_value(source_state)
        if not source_available or source_value is None:
            _LOGGER.warning(
                "%s: reset is not possible, source sensor is unavailable",
                self._source_entity_id,
            )
            return

        _LOGGER.debug(
            "%s: reset na source sensor value %.3f",
            self._source_entity_id,
            source_value,
        )

        self._last_value = source_value
        self._last_source_reset = dt_util.utcnow()
        self._is_valid = True
        self.async_write_ha_state()

    @callback
    def _handle_interval(self, now: datetime) -> None:
        expected_reset_td = self._get_expected_reset_td(now)
        if expected_reset_td is not None:
            if expected_reset_td >= self._opt_expected_overrun_td:
                self._last_value = 0.0
                self._is_valid = False
                self.async_write_ha_state()
                return

    @property
    def available(self) -> bool:
        if not self._source_available:
            if not self._opt_keep_available:
                return False

        if self._is_valid:
            return True
        if self._opt_keep_last_value:
            return True
        return False

    @property
    def native_value(self) -> float | None:
        return self._last_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "last_value": self._last_value,
            "last_source_reset": self._last_source_reset,
        }
