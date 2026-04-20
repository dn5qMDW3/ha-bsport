"""Tests for the bundled-blueprint install / remove helpers.

The helpers are pure filesystem functions that take a config-dir path, so we
can test them with plain tmp_path fixtures — no HA runtime required.
"""
from __future__ import annotations

from pathlib import Path

from custom_components.bsport import (
    _install_bundled_blueprints,
    _remove_pristine_bundled_blueprints,
)

BUNDLED_BLUEPRINT_NAME = "notify_and_book.yaml"


def test_install_copies_bundled_blueprints(tmp_path: Path) -> None:
    """First-install case — target dir empty, file should be copied verbatim."""
    written = _install_bundled_blueprints(str(tmp_path))

    assert written == [BUNDLED_BLUEPRINT_NAME]
    target = tmp_path / "blueprints" / "automation" / "bsport" / BUNDLED_BLUEPRINT_NAME
    assert target.exists()
    # Content matches what we ship.
    bundled = Path(
        "custom_components/bsport/blueprints/automation/bsport",
    ) / BUNDLED_BLUEPRINT_NAME
    assert target.read_bytes() == bundled.read_bytes()


def test_install_is_idempotent(tmp_path: Path) -> None:
    """Second call must be a no-op: no overwrite of the existing file."""
    _install_bundled_blueprints(str(tmp_path))
    second = _install_bundled_blueprints(str(tmp_path))
    assert second == []


def test_install_preserves_user_edits(tmp_path: Path) -> None:
    """If the user edits the installed blueprint, reinstall must not clobber."""
    _install_bundled_blueprints(str(tmp_path))
    target = tmp_path / "blueprints" / "automation" / "bsport" / BUNDLED_BLUEPRINT_NAME
    target.write_text("# user-edited content\n")

    second = _install_bundled_blueprints(str(tmp_path))
    assert second == []
    assert target.read_text() == "# user-edited content\n"


def test_remove_pristine_deletes_copied_blueprint(tmp_path: Path) -> None:
    """When the file is exactly what we shipped, remove it."""
    _install_bundled_blueprints(str(tmp_path))
    target = tmp_path / "blueprints" / "automation" / "bsport" / BUNDLED_BLUEPRINT_NAME
    assert target.exists()

    removed = _remove_pristine_bundled_blueprints(str(tmp_path))

    assert removed == [BUNDLED_BLUEPRINT_NAME]
    assert not target.exists()
    # Empty domain dir cleaned up too.
    assert not target.parent.exists()


def test_remove_preserves_user_modified_blueprint(tmp_path: Path) -> None:
    """If the user changed the file, uninstall leaves it alone."""
    _install_bundled_blueprints(str(tmp_path))
    target = tmp_path / "blueprints" / "automation" / "bsport" / BUNDLED_BLUEPRINT_NAME
    target.write_text("# user-edited content\n")

    removed = _remove_pristine_bundled_blueprints(str(tmp_path))

    assert removed == []
    assert target.exists()
    assert target.read_text() == "# user-edited content\n"


def test_remove_is_noop_when_nothing_was_installed(tmp_path: Path) -> None:
    removed = _remove_pristine_bundled_blueprints(str(tmp_path))
    assert removed == []
