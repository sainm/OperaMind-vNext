from pathlib import Path

from operamind.commands.profile import build_parser


def test_profile_cli_exposes_store_activate_and_read_operations() -> None:
    parser = build_parser()

    stored = parser.parse_args(
        ["store", "--profile-version-id", "profile-v1", "--profile", "profile.json"]
    )
    activated = parser.parse_args(
        [
            "activate",
            "--activation-event-id",
            "activation-1",
            "--project-id",
            "project-1",
            "--binding-key",
            "embedding:document_search",
            "--profile-version-id",
            "profile-v1",
            "--activated-by",
            "developer",
            "--reason",
            "P6 live validation",
        ]
    )
    inspected = parser.parse_args(["inspect", "--profile-version-id", "profile-v1"])
    active = parser.parse_args(
        ["active", "--project-id", "project-1", "--binding-key", "command:edit"]
    )

    assert stored.profile == Path("profile.json")
    assert activated.command == "activate"
    assert inspected.command == "inspect"
    assert active.command == "active"
