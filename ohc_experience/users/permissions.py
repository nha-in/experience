"""Platform reviewer membership; organisation roles are resolved separately."""


def is_nha_team(user) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_nha_team", False),
    )
