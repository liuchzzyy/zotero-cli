"""Profile selection must never silently redirect a requested library."""

from __future__ import annotations

from pathlib import Path

import pytest

from zotero_cli.config import ConfigError, load_config


@pytest.mark.parametrize(
    "config_text",
    [
        '[zotero]\nlibrary_id = "legacy"\napi_key = "legacy-secret"\n',
        '[zotero]\nlibrary_id = "legacy"\n[profile.main]\nlibrary_id = "main"\n',
        '[profile.main]\nlibrary_id = "main"\n',
        "",
        None,
    ],
    ids=["legacy", "mixed", "profiles-only", "empty", "missing"],
)
def test_unknown_explicit_profile_never_falls_back(tmp_path: Path, config_text: str | None) -> None:
    path = tmp_path / "config.toml"
    if config_text is not None:
        path.write_text(config_text, encoding="utf-8")

    with pytest.raises(ConfigError, match="Unknown config profile 'typo'") as error:
        load_config(path, profile="typo")

    assert "legacy-secret" not in str(error.value)
