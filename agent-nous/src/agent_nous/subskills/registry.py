from __future__ import annotations

import json
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..contracts import SchemaCatalog
from ..errors import ConflictError, ValidationError
from ..io import digest_json


def manifest_digest(value: dict[str, Any]) -> str:
    payload = dict(value)
    payload["content_hash"] = ""
    return digest_json(payload)


def _read_utf8_nofollow(path: Path, *, max_bytes: int, label: str) -> str:
    """Read one provider file from a pinned descriptor with a byte budget."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as handle:
            if os.fstat(handle.fileno()).st_size > max_bytes:
                raise ValidationError(f"{label} exceeds {max_bytes} bytes: {path}")
            content = handle.read(max_bytes + 1)
    except ValidationError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(f"{label} cannot be read safely: {path}") from exc
    if len(content) > max_bytes:
        raise ValidationError(f"{label} exceeds {max_bytes} bytes: {path}")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{label} must be UTF-8: {path}") from exc


_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def _version_key(value: str) -> tuple[Any, ...]:
    match = _SEMVER.fullmatch(value)
    if not match:
        raise ValidationError(f"subskill version must be semantic versioning: {value}")
    core = tuple(int(match.group(index)) for index in range(1, 4))
    prerelease = match.group(4)
    if prerelease is None:
        return (*core, 1, ())
    parts: list[tuple[int, int | str]] = []
    for item in prerelease.split("."):
        if item.isdigit():
            parts.append((0, int(item)))
        else:
            parts.append((1, item))
    return (*core, 0, tuple(parts))


@dataclass(frozen=True)
class SubskillProvider:
    path: Path
    manifest: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.manifest["id"])

    @property
    def phase(self) -> str:
        return str(self.manifest["phase"])

    @property
    def version(self) -> str:
        return str(self.manifest["version"])

    @property
    def content_hash(self) -> str:
        return str(self.manifest["content_hash"])

    @property
    def entrypoint(self) -> Path:
        """Return a manifest-controlled file without allowing path escape."""
        raw_candidate = self.path / str(self.manifest["entrypoint"])
        if raw_candidate.is_symlink():
            raise ValidationError(f"subskill entrypoint must be a regular file: {raw_candidate}")
        candidate = raw_candidate.resolve()
        root = self.path.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValidationError(f"subskill entrypoint escapes provider root: {self.path}")
        return candidate


class SubskillRegistry:
    MAX_MANIFEST_BYTES = 1024 * 1024

    def __init__(self, roots: Iterable[Path] = ()) -> None:
        builtin = Path(__file__).with_name("builtin")
        explicit = tuple(Path(root).expanduser().resolve() for root in roots)
        missing = [str(root) for root in explicit if not root.is_dir()]
        if missing:
            raise ValidationError("configured subskill root does not exist: " + ", ".join(missing))
        self.roots = (builtin, *explicit)
        self.schemas = SchemaCatalog()

    def discover(self) -> tuple[SubskillProvider, ...]:
        found: dict[str, SubskillProvider] = {}
        builtin_root = self.roots[0].resolve()
        for root in self.roots:
            if not root.exists():
                continue
            root_resolved = root.resolve()
            for path in sorted(root.glob("*/manifest.json")):
                provider_root = path.parent.resolve()
                if provider_root != root_resolved and root_resolved not in provider_root.parents:
                    raise ValidationError(f"subskill provider escapes configured root: {path}")
                manifest_path = path.resolve()
                if path.is_symlink() or (manifest_path != path and root_resolved not in manifest_path.parents):
                    raise ValidationError(f"subskill manifest must be a regular file inside configured root: {path}")
                value = json.loads(_read_utf8_nofollow(path, max_bytes=self.MAX_MANIFEST_BYTES, label="subskill manifest"))
                self.schemas.validate("subskill-manifest", value)
                if value["content_hash"] != manifest_digest(value):
                    raise ValidationError(f"subskill hash mismatch: {path}")
                provider = SubskillProvider(path.parent.resolve(), value)
                entrypoint = provider.entrypoint
                if not entrypoint.is_file():
                    raise ValidationError(f"missing subskill entrypoint: {entrypoint}")
                expected_guidance_hash = value.get("guidance_hash")
                if root_resolved == builtin_root and not isinstance(expected_guidance_hash, str):
                    raise ValidationError(f"built-in subskill guidance_hash is required: {path}")
                if isinstance(expected_guidance_hash, str):
                    guidance = _read_utf8_nofollow(
                        entrypoint,
                        max_bytes=128 * 1024,
                        label="subskill guidance",
                    )
                    actual_guidance_hash = hashlib.sha256(guidance.encode("utf-8")).hexdigest()
                    if actual_guidance_hash != expected_guidance_hash:
                        raise ValidationError(f"subskill guidance hash mismatch: {path}")
                existing = found.get(provider.id)
                if existing and existing.manifest["version"] == value["version"]:
                    raise ConflictError(
                        f"duplicate subskill provider {provider.id}@{value['version']}"
                    )
                # Later roots are explicit overrides, but only a higher version may replace.
                if not existing or _version_key(provider.version) > _version_key(existing.version):
                    found[provider.id] = provider
        return tuple(found[key] for key in sorted(found))

    def validate(self) -> list[str]:
        errors: list[str] = []
        try:
            providers = self.discover()
        except Exception as exc:
            return [str(exc)]
        for provider in providers:
            try:
                entrypoint = provider.entrypoint
                if not entrypoint.is_file():
                    errors.append(f"missing entrypoint: {entrypoint}")
            except ValidationError as exc:
                errors.append(str(exc))
        return errors

    def provider(self, subskill_id: str, version: str, content_hash: str) -> SubskillProvider:
        """Resolve an exact pin; never silently upgrade a persisted choice."""
        for candidate in self.discover():
            if candidate.id == subskill_id and candidate.version == version:
                if candidate.content_hash != content_hash:
                    raise ValidationError(f"subskill content hash changed: {subskill_id}@{version}")
                return candidate
        raise ValidationError(f"pinned subskill is unavailable: {subskill_id}@{version}")
