"""Config flow for bsport."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AccountProfile, BsportAuthError, BsportClient, BsportTransientError
from .const import (
    CONF_BSPORT_TOKEN,
    CONF_BSPORT_USER_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_STUDIO_ID,
    CONF_STUDIO_NAME,
    DOMAIN,
    OPT_WATCHED_OFFER_IDS,
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class BsportConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for bsport."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = BsportClient(
                session, user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            try:
                profile: AccountProfile = (
                    await client.authenticate_and_fetch_profile()
                )
            except BsportAuthError:
                errors["base"] = "invalid_auth"
            except BsportTransientError:
                return self.async_abort(reason="cannot_connect")
            except Exception:  # noqa: BLE001
                return self.async_abort(reason="unknown")
            else:
                await self.async_set_unique_id(str(profile.bsport_user_id))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{profile.studio_name} ({user_input[CONF_EMAIL]})",
                    data={
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_BSPORT_TOKEN: profile.bsport_token,
                        CONF_BSPORT_USER_ID: profile.bsport_user_id,
                        CONF_STUDIO_ID: profile.studio_id,
                        CONF_STUDIO_NAME: profile.studio_name,
                    },
                    options={OPT_WATCHED_OFFER_IDS: []},
                )
        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        from .config_flow import BsportOptionsFlow
        return BsportOptionsFlow()


class BsportOptionsFlow(config_entries.OptionsFlow):
    """Options flow — add or remove watched classes."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_watch", "remove_watch"],
        )

    async def async_step_add_watch(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        from homeassistant.helpers.selector import (
            SelectOptionDict,
            SelectSelector,
            SelectSelectorConfig,
            SelectSelectorMode,
        )

        runtime = self.config_entry.runtime_data
        if user_input is None:
            try:
                # Fetch a week's worth; bsport endpoint supports ?date=YYYY-MM-DD
                # but not ranges — for v1 we query today only. Full date-range
                # iteration is a follow-up.
                from datetime import date
                offers = await runtime.client.list_upcoming_offers(
                    company=self.config_entry.data[CONF_STUDIO_ID],
                    date=date.today().isoformat(),
                )
            except BsportTransientError:
                return self.async_abort(reason="cannot_connect")

            options = [
                SelectOptionDict(
                    value=str(o.offer_id),
                    label=f"{o.class_name} — {o.start_at.strftime('%a %d %b %H:%M')}",
                )
                for o in offers
            ]
            schema = vol.Schema(
                {
                    vol.Required("offer_id"): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.DROPDOWN,
                            custom_value=False,
                        )
                    ),
                }
            )
            return self.async_show_form(
                step_id="add_watch", data_schema=schema
            )

        new_ids = list(
            self.config_entry.options.get(OPT_WATCHED_OFFER_IDS, [])
        )
        picked = int(user_input["offer_id"])
        if picked not in new_ids:
            new_ids.append(picked)
        return self.async_create_entry(
            title="",
            data={
                **self.config_entry.options,
                OPT_WATCHED_OFFER_IDS: new_ids,
            },
        )

    async def async_step_remove_watch(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        from homeassistant.helpers.selector import (
            SelectOptionDict,
            SelectSelector,
            SelectSelectorConfig,
            SelectSelectorMode,
        )

        runtime = self.config_entry.runtime_data
        current = list(
            self.config_entry.options.get(OPT_WATCHED_OFFER_IDS, [])
        )
        if not current:
            return self.async_abort(reason="no_watches")

        labels: dict[str, str] = {}
        for oid in current:
            coord = runtime.watches.get(oid)
            if coord and coord.data:
                offer = coord.data.offer
                labels[str(oid)] = (
                    f"{offer.class_name} · "
                    f"{offer.start_at.strftime('%a %d %b %H:%M')}"
                )
            else:
                labels[str(oid)] = f"Offer #{oid}"

        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Optional("remove"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=v, label=labels[v])
                                for v in labels
                            ],
                            mode=SelectSelectorMode.LIST,
                            multiple=True,
                        )
                    ),
                }
            )
            return self.async_show_form(
                step_id="remove_watch", data_schema=schema
            )

        remove_ids = {int(x) for x in user_input.get("remove", [])}
        new_ids = [oid for oid in current if oid not in remove_ids]
        return self.async_create_entry(
            title="",
            data={
                **self.config_entry.options,
                OPT_WATCHED_OFFER_IDS: new_ids,
            },
        )
