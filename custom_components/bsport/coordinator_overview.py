"""AccountOverviewCoordinator."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    AccountOverview,
    BsportAuthError,
    BsportClient,
    BsportTransientError,
)
from .const import DOMAIN, EVENT_AUTH_FAILED, OVERVIEW_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class AccountOverviewCoordinator(DataUpdateCoordinator[AccountOverview]):
    """Fetches account overview (waitlists + bookings + membership) on a fixed cadence."""

    def __init__(
        self, hass: HomeAssistant, client: BsportClient, entry_id: str
    ):
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_overview_{entry_id}",
            update_interval=OVERVIEW_SCAN_INTERVAL,
        )
        self._client = client
        self.entry_id = entry_id

    async def _async_update_data(self) -> AccountOverview:
        try:
            return await self._client.get_account_overview()
        except BsportAuthError as err:
            self.hass.bus.async_fire(
                EVENT_AUTH_FAILED,
                {"entry_id": self.entry_id, "email": self._client_email()},
            )
            raise ConfigEntryAuthFailed(str(err)) from err
        except BsportTransientError as err:
            raise UpdateFailed(str(err)) from err

    def _client_email(self) -> str:
        return getattr(self._client, "_email", "")  # noqa: SLF001
