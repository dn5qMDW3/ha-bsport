"""Tests for BsportClient authentication."""
from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.bsport.api.client import AccountProfile, BsportClient
from custom_components.bsport.api.errors import (
    BsportAuthError,
    BsportTransientError,
)
from custom_components.bsport.const import BSPORT_API_BASE, BSPORT_SIGNIN_URL


@pytest.mark.asyncio
async def test_authenticate_stores_token_on_200():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(
                BSPORT_SIGNIN_URL,
                status=200,
                payload={
                    "status": "ok",
                    "firebaseToken": "fake.firebase.custom.token",
                    "token": "abc123def4567890abc123def4567890abc123de",
                    "email_confirmed": True,
                    "is_staff": False,
                    "is_superuser": False,
                },
            )
            client = BsportClient(session, "user@example.com", "pw")
            await client.authenticate()
    assert client._token == "abc123def4567890abc123def4567890abc123de"  # noqa: SLF001


@pytest.mark.asyncio
async def test_authenticate_raises_auth_error_on_403():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(BSPORT_SIGNIN_URL, status=403, body="")
            client = BsportClient(session, "user@example.com", "wrong")
            with pytest.raises(BsportAuthError):
                await client.authenticate()


@pytest.mark.asyncio
async def test_authenticate_raises_transient_on_network_error():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(
                BSPORT_SIGNIN_URL,
                exception=aiohttp.ClientConnectionError("boom"),
            )
            client = BsportClient(session, "user@example.com", "pw")
            with pytest.raises(BsportTransientError):
                await client.authenticate()


@pytest.mark.asyncio
async def test_authenticate_and_fetch_profile_returns_studio_metadata():
    membership_url = f"{BSPORT_API_BASE}/core-data/v1/membership/"
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(
                BSPORT_SIGNIN_URL,
                status=200,
                payload={
                    "status": "ok",
                    "firebaseToken": "fake",
                    "token": "tok_40_chars_hex_" + "0" * 23,
                    "email_confirmed": True,
                    "is_staff": False,
                    "is_superuser": False,
                },
            )
            m.get(
                membership_url,
                status=200,
                payload={
                    "count": 1,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": 12345,
                            "company": 538,
                            "company_name": "Chimosa",
                            "user_id": 9999999,
                            "consumer": 9999999,
                        }
                    ],
                },
            )
            client = BsportClient(session, "user@example.com", "pw")
            profile = await client.authenticate_and_fetch_profile(studio_id=538)
    assert profile == AccountProfile(
        bsport_token="tok_40_chars_hex_" + "0" * 23,
        bsport_user_id=9999999,
        studio_id=538,
        studio_name="Chimosa",
    )


@pytest.mark.asyncio
async def test_authenticate_and_fetch_profile_raises_when_no_membership():
    membership_url = f"{BSPORT_API_BASE}/core-data/v1/membership/"
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(
                BSPORT_SIGNIN_URL,
                status=200,
                payload={
                    "status": "ok",
                    "firebaseToken": "fake",
                    "token": "tok_40_chars_hex_" + "0" * 23,
                    "email_confirmed": True,
                    "is_staff": False,
                    "is_superuser": False,
                },
            )
            m.get(
                membership_url,
                status=200,
                payload={"count": 0, "next": None, "previous": None, "results": []},
            )
            client = BsportClient(session, "user@example.com", "pw")
            with pytest.raises(BsportAuthError):
                await client.authenticate_and_fetch_profile(studio_id=538)


@pytest.mark.asyncio
async def test_authenticate_and_fetch_profile_rejects_wrong_studio():
    membership_url = f"{BSPORT_API_BASE}/core-data/v1/membership/"
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(
                BSPORT_SIGNIN_URL,
                status=200,
                payload={
                    "status": "ok",
                    "firebaseToken": "fake",
                    "token": "tok_40_chars_hex_" + "0" * 23,
                    "email_confirmed": True,
                    "is_staff": False,
                    "is_superuser": False,
                },
            )
            m.get(
                membership_url,
                status=200,
                payload={
                    "count": 1,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": 12345,
                            "company": 538,
                            "company_name": "Chimosa",
                            "user_id": 9999999,
                            "consumer": 9999999,
                        }
                    ],
                },
            )
            client = BsportClient(session, "user@example.com", "pw")
            with pytest.raises(BsportAuthError):
                await client.authenticate_and_fetch_profile(studio_id=9999)
