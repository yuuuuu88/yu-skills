from __future__ import annotations

import pytest

from nous_pilot.distribution import (
    ReleaseManifest,
    UnsupportedRuntimeError,
    check_compatibility,
    require_compatibility,
)


def test_supported_python_and_platform_are_reported() -> None:
    report = check_compatibility(python_version_info=(3, 12, 1), platform_name="Linux")

    assert report.supported
    assert report.python_version == "3.12.1"
    assert report.platform == "Linux"
    assert report.reasons == ()


def test_old_python_is_rejected_with_a_clear_reason() -> None:
    report = check_compatibility(python_version_info=(3, 10, 13), platform_name="Linux")

    assert not report.supported
    assert "Python 3.11 or newer is required" in report.reasons[0]
    assert "3.10.13" in report.reasons[0]


def test_unknown_platform_is_rejected_with_supported_platforms() -> None:
    report = check_compatibility(python_version_info=(3, 11, 0), platform_name="FreeBSD")

    assert not report.supported
    assert "Unsupported platform 'FreeBSD'" in report.reasons[0]
    assert "Linux, macOS, and Windows" in report.reasons[0]


def test_require_compatibility_raises_for_unsupported_runtime() -> None:
    with pytest.raises(UnsupportedRuntimeError, match="does not support Python 3.10.0"):
        require_compatibility(python_version_info=(3, 10, 0), platform_name="Plan 9")


def test_release_manifest_serializes_compatibility_without_internal_dataclass_details() -> None:
    manifest = ReleaseManifest(
        version="0.1.0",
        required_python=">=3.11",
        migrations=("none",),
        compatibility=check_compatibility(python_version_info=(3, 11, 0), platform_name="Windows"),
    )

    assert manifest.as_dict() == {
        "version": "0.1.0",
        "required_python": ">=3.11",
        "migrations": ["none"],
        "compatibility": {
            "supported": True,
            "python_version": "3.11.0",
            "platform": "Windows",
            "reasons": [],
        },
    }
