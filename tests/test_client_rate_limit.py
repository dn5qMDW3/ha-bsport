"""Tests for the rate-limit pause gate."""
from __future__ import annotations

from datetime import datetime, timezone

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.bsport.api.client import BsportClient
from custom_components.bsport.api.errors import BsportRateLimited
from custom_components.bsport.const import BSPORT_API_BASE


@pytest.mark.asyncio
async def test_429_on_book_sets_pause_and_raises_rate_limited():
    compat_url = (
        f"{BSPORT_API_BASE}/buyable/v1/payment-pack/consumer-payment-pack/"
        f"compatible_with_offer_unfiltered/?mine=true"
    )
    user_reg_url = f"{BSPORT_API_BASE}/book/v1/offer/user_registration/"
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(compat_url, status=200, payload=[{"id": 1}])
            m.post(
                user_reg_url,
                status=429,
                headers={"Retry-After": "0.05"},
                body="",
            )
            client = BsportClient(session, "u@example.com", "pw")
            client._token = "fake"  # noqa: SLF001
            with pytest.raises(BsportRateLimited):
                await client.book_offer(42)
    assert client._pause_until is not None  # noqa: SLF001
    remaining = (
        client._pause_until - datetime.now(timezone.utc)  # noqa: SLF001
    ).total_seconds()
    assert -0.01 <= remaining <= 0.06
