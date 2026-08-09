from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .errors import ValidationError


@dataclass(frozen=True)
class DurabilityProfile:
    """Describe the guarantees of the file-backed store without overclaiming.

    ``shared_fs`` is safe only when all workers see the same POSIX filesystem
    and its flock/rename semantics. It is not a distributed database.
    """

    mode: Literal["local", "shared_fs"] = "local"
    host_id: str = ""

    def __post_init__(self) -> None:
        if self.mode not in {"local", "shared_fs"}:
            raise ValidationError("durability mode must be local or shared_fs")
        if self.mode == "shared_fs" and not self.host_id:
            object.__setattr__(self, "host_id", f"{platform.node()}:{os.getpid()}")

    @property
    def capabilities(self) -> dict[str, Any]:
        return {
            "store": "file-backed-taskstore",
            "lock": "POSIX advisory flock",
            "commit": "atomic os.replace within one filesystem",
            "mode": self.mode,
            "multi_host": self.mode == "shared_fs",
            "multi_host_condition": "all hosts use one shared POSIX filesystem with supported flock/rename semantics" if self.mode == "shared_fs" else "single host or one shared filesystem must be selected explicitly",
            "distributed_database": False,
            "host_id": self.host_id or None,
        }

    def validate_home(self, home: Path) -> None:
        resolved = home.expanduser().resolve()
        if resolved == Path("/"):
            raise ValidationError("durability home cannot be filesystem root")
        if self.mode == "shared_fs" and resolved.is_symlink():
            raise ValidationError("shared_fs durability home must not be a symlink")


def durability_report(home: Path, mode: Literal["local", "shared_fs"] = "local") -> dict[str, Any]:
    profile = DurabilityProfile(mode=mode)
    profile.validate_home(home)
    return {"home": str(home.expanduser().resolve()), **profile.capabilities}
