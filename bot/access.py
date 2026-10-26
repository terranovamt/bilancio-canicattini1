from __future__ import annotations

from collections.abc import Set


def is_authorized_user(
    user_id: int | None,
    allowed_user_ids: Set[int],
) -> bool:
    return user_id is not None and user_id in allowed_user_ids


def is_admin_user(user_id: int | None, admin_user_id: int) -> bool:
    return user_id is not None and user_id == admin_user_id
