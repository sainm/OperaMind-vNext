import json
from pathlib import Path
from typing import Any

import pytest

from operamind.domain import SafeCommandTemplate

ROOT = Path(__file__).parents[2]


def _profile() -> dict[str, Any]:
    raw: object = json.loads(
        (ROOT / "profiles/command-execution-profile.example.json").read_text(encoding="utf-8")
    )
    assert isinstance(raw, dict)
    return raw


def test_safe_command_template_resolves_an_exact_profile_entry() -> None:
    template = SafeCommandTemplate.from_profile(_profile(), command_ref="targeted-unit")

    assert template.argv == ("./gradlew", "test", "--no-daemon")
    assert template.failure_policy == "record_and_block"
    assert len(template.digest) == 64


def test_safe_command_template_rejects_unknown_ref() -> None:
    with pytest.raises(ValueError, match="does not define"):
        SafeCommandTemplate.from_profile(_profile(), command_ref="not-approved")
