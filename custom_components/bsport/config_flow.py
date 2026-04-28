"""Config flow for bsport."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import AccountProfile, BsportAuthError, BsportClient, BsportTransientError
from .const import (
    CONF_BSPORT_TOKEN,
    CONF_BSPORT_USER_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_STUDIO_COVER,
    CONF_STUDIO_ID,
    CONF_STUDIO_NAME,
    DEFAULT_AUTO_BOOK_LEAD_TIME,
    DOMAIN,
    KNOWN_STUDIOS,
    MAX_AUTO_BOOK_LEAD_TIME_HOURS,
    OPT_AUTO_BOOK_LEAD_TIME,
    OPT_WATCHED_OFFER_IDS,
)

# Sentinel chosen from the dropdown when the user's studio isn't in the
# curated list. Picking it routes to the custom_studio step which asks for
# the numeric bsport company id.
STUDIO_OTHER_SENTINEL = "__other__"

# Sort the curated studios alphabetically by name (case-insensitive) so the
# dropdown reads like a directory rather than a numeric-id sequence. The
# KNOWN_STUDIOS tuple in const.py stays sorted by id for stable diffs in
# the weekly auto-update PRs; this presentation sort is UI-only.
#
# The "Other" sentinel is appended after the sorted studios so it always
# lives at the bottom of the list regardless of alphabetization.
#
# Searchability: HA's SelectSelector with mode=DROPDOWN automatically
# offers typeahead filtering on the option labels in the frontend — no
# extra config needed.
_STUDIO_OPTIONS = [
    SelectOptionDict(value=str(sid), label=name)
    for sid, name in sorted(KNOWN_STUDIOS, key=lambda s: s[1].casefold())
] + [
    SelectOptionDict(
        value=STUDIO_OTHER_SENTINEL,
        label="Other (enter company id)",
    ),
]

# Dropdown without `custom_value` so HA's selector renders the selected
# option's label after pick instead of its raw value (the numeric id).
STUDIO_PICK_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_STUDIO_ID): SelectSelector(
            SelectSelectorConfig(
                options=_STUDIO_OPTIONS,
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
    }
)

CUSTOM_STUDIO_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_STUDIO_ID): vol.All(
            vol.Coerce(int), vol.Range(min=1),
        ),
    }
)

CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class BsportConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Two-step config flow: pick studio, then enter credentials."""

    VERSION = 1
    MINOR_VERSION = 2  # bumped from 1.1 — unique_id format changed

    def __init__(self) -> None:
        self._studio_id: int | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Pick the studio."""
        errors: dict[str, str] = {}
        if user_input is not None:
            raw = user_input[CONF_STUDIO_ID]
            if raw == STUDIO_OTHER_SENTINEL:
                return await self.async_step_custom_studio()
            try:
                self._studio_id = int(raw)
            except (TypeError, ValueError):
                errors["base"] = "invalid_studio_id"
            else:
                return await self.async_step_credentials()
        return self.async_show_form(
            step_id="user", data_schema=STUDIO_PICK_SCHEMA, errors=errors
        )

    async def async_step_custom_studio(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for a numeric bsport company id when the studio isn't on the list."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._studio_id = int(user_input[CONF_STUDIO_ID])
            except (TypeError, ValueError):
                errors["base"] = "invalid_studio_id"
            else:
                return await self.async_step_credentials()
        return self.async_show_form(
            step_id="custom_studio",
            data_schema=CUSTOM_STUDIO_SCHEMA,
            errors=errors,
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Enter credentials and verify membership."""
        assert self._studio_id is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = BsportClient(
                session, user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            try:
                profile: AccountProfile = (
                    await client.authenticate_and_fetch_profile(
                        studio_id=self._studio_id
                    )
                )
            except BsportAuthError as err:
                # Distinguish wrong-credentials from wrong-studio.
                if "not a member" in str(err).lower():
                    errors["base"] = "not_a_member"
                else:
                    errors["base"] = "invalid_auth"
            except BsportTransientError:
                return self.async_abort(reason="cannot_connect")
            except Exception:  # noqa: BLE001
                return self.async_abort(reason="unknown")
            else:
                await self.async_set_unique_id(
                    f"{profile.studio_id}:{profile.bsport_user_id}"
                )
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
                        CONF_STUDIO_COVER: profile.studio_cover,
                    },
                    options={OPT_WATCHED_OFFER_IDS: []},
                )
        return self.async_show_form(
            step_id="credentials",
            data_schema=CREDENTIALS_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        from .config_flow import BsportOptionsFlow
        return BsportOptionsFlow()


class BsportOptionsFlow(config_entries.OptionsFlow):
    """Options flow — add or remove watched classes."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_watch", "remove_watch", "set_auto_book_lead_time",
            ],
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

    async def async_step_set_auto_book_lead_time(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Set the global auto-book minimum lead time, in hours."""
        current_seconds = self.config_entry.options.get(
            OPT_AUTO_BOOK_LEAD_TIME,
            int(DEFAULT_AUTO_BOOK_LEAD_TIME.total_seconds()),
        )
        current_hours = int(current_seconds // 3600)
        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Required("hours", default=current_hours): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=0, max=MAX_AUTO_BOOK_LEAD_TIME_HOURS),
                    ),
                }
            )
            return self.async_show_form(
                step_id="set_auto_book_lead_time",
                data_schema=schema,
            )

        new_seconds = int(user_input["hours"]) * 3600
        return self.async_create_entry(
            title="",
            data={
                **self.config_entry.options,
                OPT_AUTO_BOOK_LEAD_TIME: new_seconds,
            },
        )
