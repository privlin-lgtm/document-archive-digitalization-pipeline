from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AuthPrincipal:
    """Authenticated caller. Reviewer is always server-derived."""

    reviewer: str
    via: Literal["session", "bearer"]
