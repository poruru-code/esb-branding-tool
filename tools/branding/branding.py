# Where: tools/branding/branding.py
# What: Branding derivation and validation helpers.
# Why: Derive branded tokens from a single brand identifier.
from __future__ import annotations

import re
from dataclasses import dataclass


class BrandingError(ValueError):
    """Raised when branding configuration is invalid."""


_ENV_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_LABEL_PREFIX_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")


@dataclass(frozen=True)
class Branding:
    cli_name: str
    slug: str
    env_prefix: str
    label_prefix: str
    paths: dict[str, str]
    root_ca: dict[str, str]
    runtime: dict[str, str]


def derive_branding(brand: str) -> Branding:
    brand = _require_brand(brand)
    slug = _normalize_slug(brand)
    env_prefix = _normalize_env_prefix(brand)
    cli_name = slug
    label_prefix = f"com.{slug}"

    paths = {
        "home_dir": f".{slug}",
        "output_dir": f".{slug}",
        "staging_dir": ".staging",
    }
    root_ca = {
        "secret_id": f"{slug}_root_ca",
        "cert_filename": "rootCA.crt",
    }
    runtime = {
        "container_prefix": slug,
        "namespace": slug,
        "cni_name": f"{slug}-net",
        "cni_bridge": f"{slug}0",
        "cni_dir": f"/run/{slug}/cni",
        "resolv_conf_path": f"/run/containerd/{slug}/resolv.conf",
        "label_env": f"{slug}_env",
        "label_function": f"{slug}_function",
        "label_created_by": "created_by",
        "label_created_by_value": f"{slug}-agent",
        "cgroup_parent": slug,
        "cgroup_leaf": "runtime-node",
    }

    _validate_pattern("cli_name", cli_name, _SLUG_RE)
    _validate_pattern("slug", slug, _SLUG_RE)
    _validate_pattern("env_prefix", env_prefix, _ENV_PREFIX_RE)
    _validate_pattern("label_prefix", label_prefix, _LABEL_PREFIX_RE)

    return Branding(
        cli_name=cli_name,
        slug=slug,
        env_prefix=env_prefix,
        label_prefix=label_prefix,
        paths=paths,
        root_ca=root_ca,
        runtime=runtime,
    )


def build_context(branding: Branding) -> dict[str, str]:
    env_prefix_var = "${" + branding.env_prefix
    return {
        "CLI_NAME": branding.cli_name,
        "SLUG": branding.slug,
        "ENV_PREFIX": branding.env_prefix,
        "ENV_PREFIX_VAR": env_prefix_var,
        "LABEL_PREFIX": branding.label_prefix,
        "HOME_DIR": branding.paths["home_dir"],
        "OUTPUT_DIR": branding.paths["output_dir"],
        "STAGING_DIR": branding.paths["staging_dir"],
        "ROOT_CA_MOUNT_ID": branding.root_ca["secret_id"],
        "ROOT_CA_CERT_FILENAME": branding.root_ca["cert_filename"],
        "RUNTIME_CONTAINER_PREFIX": branding.runtime["container_prefix"],
        "RUNTIME_NAMESPACE": branding.runtime["namespace"],
        "RUNTIME_CNI_NAME": branding.runtime["cni_name"],
        "RUNTIME_CNI_BRIDGE": branding.runtime["cni_bridge"],
        "RUNTIME_CNI_DIR": branding.runtime["cni_dir"],
        "RUNTIME_RESOLV_CONF_PATH": branding.runtime["resolv_conf_path"],
        "RUNTIME_LABEL_ENV": branding.runtime["label_env"],
        "RUNTIME_LABEL_FUNCTION": branding.runtime["label_function"],
        "RUNTIME_LABEL_CREATED_BY": branding.runtime["label_created_by"],
        "RUNTIME_LABEL_CREATED_BY_VALUE": branding.runtime["label_created_by_value"],
        "RUNTIME_CGROUP_PARENT": branding.runtime["cgroup_parent"],
        "RUNTIME_CGROUP_LEAF": branding.runtime["cgroup_leaf"],
    }


def _require_brand(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BrandingError("brand must be a non-empty string")
    return value.strip()


def _validate_pattern(key: str, value: str, pattern: re.Pattern[str]) -> None:
    if not pattern.fullmatch(value):
        raise BrandingError(f"{key} has invalid format: {value!r}")


def _normalize_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not cleaned:
        raise BrandingError("brand must include at least one alphanumeric character")
    return cleaned


def _normalize_env_prefix(value: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        raise BrandingError("brand must start with a letter for env_prefix derivation")
    return cleaned
