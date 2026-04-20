"""Every config-flow schema must be JSON-serialisable."""
from __future__ import annotations

import voluptuous_serialize
from homeassistant.helpers import config_validation as cv

from custom_components.bsport.config_flow import USER_SCHEMA


def test_user_schema_is_serializable():
    voluptuous_serialize.convert(
        USER_SCHEMA, custom_serializer=cv.custom_serializer
    )
