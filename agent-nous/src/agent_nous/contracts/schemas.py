from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, RefResolver
from jsonschema.exceptions import SchemaError as JsonSchemaError
from jsonschema.exceptions import ValidationError as JsonValidationError

from ..errors import ValidationError


class SchemaCatalog:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).with_name("schemas")
        self._schemas: dict[str, dict[str, Any]] = {}
        for path in sorted(self.root.glob("*.schema.json")):
            with path.open(encoding="utf-8") as handle:
                schema = json.load(handle)
            self._schemas[path.name.removesuffix(".schema.json")] = schema

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._schemas)

    def schema(self, name: str) -> dict[str, Any]:
        try:
            return self._schemas[name]
        except KeyError as exc:
            raise ValidationError(f"unknown schema: {name}") from exc

    def validate(self, name: str, value: Any) -> None:
        schema = self.schema(name)
        resolver = RefResolver(base_uri=self.root.as_uri() + "/", referrer=schema)
        try:
            Draft202012Validator(
                schema, resolver=resolver, format_checker=FormatChecker()
            ).validate(value)
        except (JsonValidationError, JsonSchemaError) as exc:
            path = ".".join(str(item) for item in getattr(exc, "absolute_path", ()))
            location = f" at {path}" if path else ""
            raise ValidationError(f"{name}{location}: {exc.message}") from exc

    def check_all(self) -> list[str]:
        errors: list[str] = []
        for name, schema in self._schemas.items():
            try:
                Draft202012Validator.check_schema(schema)
                self.validate_references(name, schema)
            except Exception as exc:  # report every schema in doctor output
                errors.append(f"{name}: {exc}")
        return errors

    def validate_references(self, name: str, value: Any) -> None:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and not ref.startswith("#"):
                filename = ref.split("#", 1)[0]
                if not (self.root / filename).is_file():
                    raise ValidationError(f"{name}: unresolved $ref {ref}")
            for nested in value.values():
                self.validate_references(name, nested)
        elif isinstance(value, list):
            for nested in value:
                self.validate_references(name, nested)
