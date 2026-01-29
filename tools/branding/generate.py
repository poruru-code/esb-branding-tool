# Where: tools/branding/generate.py
# What: Render branding templates into concrete files.
# Why: Regenerate branded assets from a single source of truth.
from __future__ import annotations

import argparse
import difflib
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TEMPLATE_ROOT = REPO_ROOT


def _import_branding_modules():
    from tools.branding.branding import (
        Branding,
        BrandingError,
        build_context,
        derive_branding,
    )

    return Branding, BrandingError, build_context, derive_branding


Branding, BrandingError, build_context, derive_branding = _import_branding_modules()


ENV_FILE = ".branding.env"
ESB_INFO_FILE = ".esb-info"
LOCK_FILE = "branding.lock"
BRANDING_CONFIG_PATH = Path("config/branding.yaml")


class TemplateSpec(NamedTuple):
    template: str
    target: str


TEMPLATES: tuple[TemplateSpec, ...] = (
    TemplateSpec(
        "tools/branding/templates/config/defaults.env.tmpl", "config/defaults.env"
    ),
    TemplateSpec("tools/branding/templates/Makefile.tmpl", "Makefile"),
    TemplateSpec("tools/branding/templates/meta/meta.go.tmpl", "meta/meta.go"),
    TemplateSpec(
        "tools/branding/templates/docker-compose.docker.yml.tmpl",
        "docker-compose.docker.yml",
    ),
    TemplateSpec(
        "tools/branding/templates/docker-compose.containerd.yml.tmpl",
        "docker-compose.containerd.yml",
    ),
    TemplateSpec(
        "tools/branding/templates/docker-compose.fc.yml.tmpl",
        "docker-compose.fc.yml",
    ),
    TemplateSpec(
        "tools/branding/templates/docker-compose.fc-node.yml.tmpl",
        "docker-compose.fc-node.yml",
    ),
    TemplateSpec(
        "tools/branding/templates/docker-bake.hcl.tmpl",
        "docker-bake.hcl",
    ),
)

_PLACEHOLDER_RE = re.compile(r"{{\s*([A-Z0-9_]+)\s*}}")


def main() -> int:
    args = parse_args()
    try:
        lock_data = load_lock_data(REPO_ROOT / LOCK_FILE)
        validate_tool_commit(lock_data, REPO_ROOT, skip=args.force)
        root = resolve_repo_root(args.root)
        brand_name = resolve_brand(args.brand, root, check=args.check)
        ensure_esb_info(
            root, brand_name, args.esb_base, check=args.check, force=args.force
        )
        print(f"==== BRANDING: {brand_name} ====")
        branding = derive_branding(brand_name)
        if not args.check:
            write_branding_env(root, branding, brand_name)
        context = build_context(branding)
        mismatches = render_templates(
            root,
            context,
            args.check,
            args.verbose,
        )
    except BrandingError as exc:
        print(f"branding error: {exc}")
        return 1

    if args.check and mismatches:
        print("branding templates out of date:")
        for path in mismatches:
            print(f"  - {path}")
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render branding templates")
    parser.add_argument(
        "-r", "--root", type=Path, default=None, help="Repository root override"
    )
    parser.add_argument(
        "-b",
        "--brand",
        default=None,
        help="Brand identifier (defaults to config/branding.yaml)",
    )
    parser.add_argument(
        "--esb-base",
        default=None,
        help="ESB base commit/tag for downstream tracking (.esb-info)",
    )
    parser.add_argument(
        "--check", action="store_true", help="Check if outputs are up to date"
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Skip tool commit validation (warn instead of error)",
    )
    parser.add_argument("--verbose", action="store_true", help="Print rendered outputs")
    parser.add_argument(
        "--no-header",
        action="store_true",
        help="Skip writing auto-generated headers when writing files",
    )
    return parser.parse_args()


def resolve_repo_root(root: Path | None) -> Path:
    if root is not None:
        return root.resolve()
    start = Path.cwd().resolve()
    for candidate in [start, *start.parents]:
        if (candidate / "docker-compose.yml").exists():
            return candidate
    raise BrandingError("repository root not found (docker-compose.yml missing)")


def resolve_brand(brand: str | None, root: Path, *, check: bool = False) -> str:
    config_path = root / BRANDING_CONFIG_PATH
    config_brand = load_brand_from_config(config_path)
    if brand is not None and brand.strip():
        brand_value = brand.strip()
        if config_brand and config_brand != brand_value:
            if check:
                raise BrandingError(
                    f"brand mismatch for --check (config={config_brand}, requested={brand_value})"
                )
            # DO NOT auto-update branding.yaml when explicitly requested via --brand
        elif not config_brand:
            # DO NOT auto-create branding.yaml when explicitly requested via --brand
            if check:
                return brand_value
        return brand_value
    if config_brand:
        return config_brand
    raise BrandingError("brand is required (use --brand or set config/branding.yaml)")


def load_lock_data(path: Path) -> dict[str, str]:
    if not path.exists():
        raise BrandingError(f"{LOCK_FILE} not found in tool repo")
    data: dict[str, str] = {}
    stack: list[dict[str, str]] = []
    prefixes: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        while stack and indent < len(stack) * 2:
            stack.pop()
            prefixes.pop()
        if ":" not in stripped:
            continue
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not raw_value:
            if stack:
                prefixes.append(f"{prefixes[-1]}{key}.")
            else:
                prefixes.append(f"{key}.")
            stack.append({})
            continue
        value = _strip_quotes(raw_value)
        prefix = prefixes[-1] if prefixes else ""
        data[f"{prefix}{key}"] = value
    return data


def validate_tool_commit(
    lock_data: dict[str, str], tool_root: Path, *, skip: bool = False
) -> None:
    expected = lock_data.get("tool.commit")
    if not expected:
        raise BrandingError("branding.lock missing tool.commit")
    current = git_rev_parse(tool_root)
    if current != expected:
        if skip:
            print(
                f"WARNING: tool repo commit mismatch "
                f"(expected={expected[:8]}, current={current[:8]})",
                file=sys.stderr,
            )
            return
        raise BrandingError(
            "tool repo commit mismatch (checkout tool.commit from branding.lock)"
        )


def ensure_esb_info(
    root: Path,
    brand: str,
    esb_base: str | None,
    *,
    check: bool,
    force: bool = False,
) -> None:
    if brand == "esb":
        return
    info_path = root / ESB_INFO_FILE
    info = load_esb_info(info_path)
    if not info:
        if not esb_base:
            if force:
                print(
                    f"WARNING: {ESB_INFO_FILE} missing (skipped with --force)",
                    file=sys.stderr,
                )
                return
            raise BrandingError(
                f"{ESB_INFO_FILE} missing (use --esb-base to create it)"
            )
        if check:
            raise BrandingError(
                f"{ESB_INFO_FILE} missing for --check (rerun without --check)"
            )
        key, value = normalize_esb_base(esb_base)
        write_esb_info(info_path, key, value)
        return
    if not has_esb_base(info):
        raise BrandingError(f"{ESB_INFO_FILE} missing ESB base entry")
    if esb_base:
        key, value = normalize_esb_base(esb_base)
        existing = info.get(key)
        if existing:
            if existing != value:
                if check:
                    raise BrandingError(
                        f"{ESB_INFO_FILE} mismatch for {key} (expected {existing})"
                    )
                write_esb_info(info_path, key, value)
        else:
            if check:
                raise BrandingError(
                    f"{ESB_INFO_FILE} missing {key} (rerun without --check)"
                )
            write_esb_info(info_path, key, value)


def load_esb_info(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            data[key] = value
    return data


def normalize_esb_base(value: str) -> tuple[str, str]:
    value = value.strip()
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", value):
        return "ESB_BASE_COMMIT", value
    return "ESB_BASE_TAG", value


def has_esb_base(info: dict[str, str]) -> bool:
    return bool(info.get("ESB_BASE_COMMIT") or info.get("ESB_BASE_TAG"))


def write_esb_info(path: Path, key: str, value: str) -> None:
    content = "\n".join(
        [
            "# Auto-generated by branding generator. DO NOT EDIT.",
            "# Tracks downstream ESB base commit/tag for patching.",
            f"{key}={value}",
            "",
        ]
    )
    path.write_text(content, encoding="utf-8")


def git_rev_parse(path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        raise BrandingError(f"git rev-parse failed: {stderr}") from exc
    return result.stdout.strip()


def load_brand_from_config(path: Path) -> str | None:
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").splitlines()
    for line in raw:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not stripped.startswith("brand:"):
            continue
        value = stripped.split(":", 1)[1].strip()
        value = _strip_inline_comment(value)
        value = _strip_quotes(value)
        if value:
            return value
        break
    raise BrandingError("config/branding.yaml missing 'brand' value")


def _strip_inline_comment(value: str) -> str:
    if not value:
        return value
    if value.startswith(("'", '"')):
        quote = value[0]
        end = value.find(quote, 1)
        if end != -1:
            return value[: end + 1]
        return value
    return value.split("#", 1)[0].strip()


def _strip_quotes(value: str) -> str:
    if len(value) < 2:
        return value
    if value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def write_brand_config(path: Path, brand: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        [
            "# Where: config/branding.yaml",
            "# What: Branding identifier for generator defaults.",
            "# Why: Keep branding reproducible across clones.",
            f"brand: {brand}",
        ]
    )
    content += "\n"
    path.write_text(content, encoding="utf-8")


def render_templates(
    root: Path,
    context: dict[str, str],
    check: bool,
    verbose: bool,
    *,
    strip_header: bool = False,
) -> list[str]:
    mismatches: list[str] = []
    for spec in TEMPLATES:
        template_path = TEMPLATE_ROOT / spec.template
        target_path = root / render_string(spec.target, context)
        template = template_path.read_text(encoding="utf-8")
        rendered = render_string(template, context)
        if strip_header:
            rendered = remove_header(rendered, template_path)

        if check:
            if not target_path.exists():
                mismatches.append(str(target_path))
                continue
            existing = target_path.read_text(encoding="utf-8")
            if existing != rendered:
                mismatches.append(str(target_path))
                print(f"Diff for {target_path}:")
                diff = difflib.unified_diff(
                    existing.splitlines(keepends=True),
                    rendered.splitlines(keepends=True),
                    fromfile="current",
                    tofile="generated",
                )
                print("".join(diff))
            continue

        if verbose:
            print(f"render {template_path} -> {target_path}")
        write_file(target_path, rendered)
    return mismatches


def render_string(template: str, context: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            raise BrandingError(f"unknown template key: {key}")
        return context[key]

    rendered = _PLACEHOLDER_RE.sub(replace, template)
    if _PLACEHOLDER_RE.search(rendered):
        raise BrandingError("unresolved template placeholders detected")
    return rendered


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = None
    if path.exists():
        mode = path.stat().st_mode & 0o777
    else:
        # Default for generated files. Executable if .sh.
        mode = 0o755 if path.suffix == ".sh" else 0o644
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)


def write_branding_env(root: Path, branding: Branding, brand_name: str) -> Path:
    path = root / ENV_FILE
    env_vars = (
        ("BRANDING_NAME", brand_name),
        ("BRANDING_CLI_NAME", branding.cli_name),
        ("BRANDING_SLUG", branding.slug),
        ("BRANDING_ENV_PREFIX", branding.env_prefix),
        ("BRANDING_LABEL_PREFIX", branding.label_prefix),
    )
    lines = [
        "# Auto-generated by branding generator. DO NOT EDIT.",
        "# Source this file to populate common branding identifiers.",
    ]
    for name, value in env_vars:
        lines.append(f"export {name}={shlex.quote(value)}")
    content = "\n".join(lines) + "\n"
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o644)
    os.environ.update({name: value for name, value in env_vars})
    return path


def remove_header(content: str, template_path: Path) -> str:
    if template_path.name.endswith(".conflist.tmpl"):
        return _strip_json_comment(content)
    return _strip_comment_header(content)


def _strip_json_comment(content: str) -> str:
    lines = content.splitlines()
    result: list[str] = []
    skipped = False
    for line in lines:
        stripped = line.lstrip()
        if not skipped and '"_comment"' in stripped:
            skipped = True
            continue
        result.append(line)
    result_text = "\n".join(result)
    if content.endswith("\n"):
        result_text += "\n"
    return result_text


def _strip_comment_header(content: str) -> str:
    lines = content.splitlines()
    idx = 0
    prefix: list[str] = []
    if lines and lines[0].startswith("#!"):
        prefix.append(lines[0])
        idx = 1
    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()
        if not stripped:
            idx += 1
            continue
        if stripped.startswith("#") or stripped.startswith("//"):
            idx += 1
            continue
        break
    remainder = lines[idx:]
    trimmed = list(remainder)
    while trimmed and not trimmed[0].strip():
        trimmed.pop(0)
    if prefix:
        result_lines = (
            prefix + ([""] if trimmed and trimmed[0].strip() else []) + trimmed
        )
    else:
        result_lines = trimmed
    result_text = "\n".join(result_lines)
    if content.endswith("\n"):
        result_text += "\n"
    return result_text


if __name__ == "__main__":
    raise SystemExit(main())
