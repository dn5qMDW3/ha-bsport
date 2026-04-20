"""Every config-flow schema must be JSON-serialisable."""
from __future__ import annotations

import voluptuous_serialize
from homeassistant.helpers import config_validation as cv

from custom_components.bsport.config_flow import (
    CREDENTIALS_SCHEMA,
    STUDIO_PICK_SCHEMA,
)


def test_studio_pick_schema_is_serializable():
    voluptuous_serialize.convert(
        STUDIO_PICK_SCHEMA, custom_serializer=cv.custom_serializer
    )


def test_credentials_schema_is_serializable():
    voluptuous_serialize.convert(
        CREDENTIALS_SCHEMA, custom_serializer=cv.custom_serializer
    )
