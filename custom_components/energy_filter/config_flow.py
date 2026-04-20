from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, UnitOfEnergy
from homeassistant.core import callback
from homeassistant.helpers.selector import selector
from homeassistant.util import slugify

from .const import (
    CONF_NAME,
    CONF_SOURCE_ENTITY,
    CONF_UNIT_OF_MEASUREMENT,
    DEFAULT_EXPECTED_OVERRUN_MINUTES,
    DEFAULT_EXPECTED_RESET_INTERVAL,
    DEFAULT_KEEP_AVAILABLE,
    DEFAULT_KEEP_LAST_VALUE,
    DOMAIN,
    OPT_EXPECTED_OVERRUN_MINUTES,
    OPT_EXPECTED_RESET_INTERVAL,
    OPT_KEEP_AVAILABLE,
    OPT_KEEP_LAST_VALUE,
    ResetInterval,
)


class EnergyFilterConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._source_entity_id: str
        self._name: str
        self._unit_of_measurement: str

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            self._source_entity_id = user_input[CONF_SOURCE_ENTITY]
            source_state = self.hass.states.get(self._source_entity_id)
            if source_state is None:
                errors["base"] = "invalid_sensor"
            else:
                self._name = source_state.name
                self._unit_of_measurement = (
                    source_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
                    or UnitOfEnergy.KILO_WATT_HOUR
                )

            if not errors:
                unique_id = slugify(self._source_entity_id)
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                return await self.async_step_options()

        data_schema = vol.Schema(
            {
                vol.Required(CONF_SOURCE_ENTITY): selector(
                    {
                        "entity": {
                            "device_class": "energy",
                            "domain": "sensor",
                        }
                    }
                ),
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_options(self, user_input=None):
        errors = {}

        if user_input is not None:
            # options = {
            #     OPT_KEEP_AVAILABLE: user_input[OPT_KEEP_AVAILABLE],
            #     OPT_KEEP_LAST_VALUE: user_input[OPT_KEEP_LAST_VALUE],
            #     OPT_EXPECTED_RESET_INTERVAL: user_input[OPT_EXPECTED_RESET_INTERVAL],
            #     OPT_EXPECTED_OVERRUN_MINUTES: user_input[OPT_EXPECTED_OVERRUN_MINUTES],
            # }

            if not errors:
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Energy Filter - {self._name}",
                    data={
                        CONF_SOURCE_ENTITY: self._source_entity_id,
                        CONF_NAME: self._name,
                        CONF_UNIT_OF_MEASUREMENT: self._unit_of_measurement,
                    },
                    options=user_input,
                )

        options = {}
        data_schema = OptionsFlowHandler.build_data_schema(options)

        return self.async_show_form(
            step_id="options",
            data_schema=data_schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler()


class OptionsFlowHandler(OptionsFlow):
    @staticmethod
    def build_data_schema(options: dict) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(
                    OPT_KEEP_AVAILABLE,
                    default=options.get(OPT_KEEP_AVAILABLE, DEFAULT_KEEP_AVAILABLE),
                ): bool,
                vol.Required(
                    OPT_KEEP_LAST_VALUE,
                    default=options.get(OPT_KEEP_LAST_VALUE, DEFAULT_KEEP_LAST_VALUE),
                ): bool,
                vol.Required(
                    OPT_EXPECTED_RESET_INTERVAL,
                    default=options.get(
                        OPT_EXPECTED_RESET_INTERVAL, DEFAULT_EXPECTED_RESET_INTERVAL
                    ),
                ): selector(
                    {
                        "select": {
                            "options": [m.name for m in ResetInterval],
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Required(
                    OPT_EXPECTED_OVERRUN_MINUTES,
                    default=options.get(
                        OPT_EXPECTED_OVERRUN_MINUTES, DEFAULT_EXPECTED_OVERRUN_MINUTES
                    ),
                ): selector(
                    {
                        "number": {
                            "min": 0,
                            "max": 59,
                            "unit_of_measurement": "min",
                            "mode": "box",
                        }
                    }
                ),
            }
        )

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="Options", data=user_input)

        options = self.config_entry.options.copy()
        data_schema = self.build_data_schema(options)
        return self.async_show_form(step_id="init", data_schema=data_schema)
