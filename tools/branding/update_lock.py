# Where: tools/branding/update_lock.py
# What: Update branding.lock with resolved ESB/tool metadata.
# Why: Keep branding.lock consistent and reproducible in CI.
from __future__ import annotations

import argparse
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

LOCK_SCHEMA_VERSION = 1


class LockError(RuntimeError):
    """Raised when branding.lock update fails."""


def main() -> int:
    args = parse_args()
    tool_root = Path(__file__).resolve().parents[2]
    esb_dir = args.esb_dir.resolve()

    esb_repo = args.esb_repo or _read_git_remote(esb_dir)
    if not esb_repo:
        raise LockError("esb_repo is required (pass --esb-repo)")
    if "://" not in esb_repo and "/" in esb_repo:
        esb_repo = f"https://github.com/{esb_repo}.git"

    tool_commit = _git_rev_parse(tool_root)
    tool_ref = _git_exact_ref(tool_root)
    esb_commit = _git_rev_parse(esb_dir)

    esb_ref = _normalize_ref(args.esb_ref)
    new_data = {
        "schema_version": str(LOCK_SCHEMA_VERSION),
        "tool.commit": tool_commit,
        "tool.ref": tool_ref,
        "source.esb_repo": esb_repo,
        "source.esb_commit": esb_commit,
        "source.esb_ref": esb_ref,
        "parameters.brand": args.brand,
    }

    lock_path = tool_root / args.lock_file
    existing = _read_lock(lock_path)
    if existing and _equivalent_lock(existing, new_data):
        return 0

    locked_at = _now_iso()
    content = _render_lock(
        schema_version=LOCK_SCHEMA_VERSION,
        locked_at=locked_at,
        tool_commit=tool_commit,
        tool_ref=tool_ref,
        esb_repo=esb_repo,
        esb_commit=esb_commit,
        esb_ref=esb_ref,
        brand=args.brand,
    )
    lock_path.write_text(content, encoding="utf-8")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update branding.lock")
    parser.add_argument("--esb-dir", type=Path, required=True, help="Path to ESB checkout")
    parser.add_argument("--esb-repo", default=None, help="ESB repository URL or owner/repo")
    parser.add_argument("--esb-ref", default=None, help="ESB ref (tag/branch) if provided")
    parser.add_argument("--brand", required=True, help="Brand identifier")
    parser.add_argument("--lock-file", default="branding.lock", help="Path to branding.lock")
    return parser.parse_args()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _git_rev_parse(path: Path) -> str:
    return _git(path, ["rev-parse", "HEAD"])


def _git_exact_ref(path: Path) -> str | None:
    try:
        return _git(path, ["describe", "--tags", "--exact-match"])
    except LockError:
        return None


def _read_git_remote(path: Path) -> str | None:
    try:
        return _git(path, ["remote", "get-url", "origin"])
    except LockError:
        return None


def _git(path: Path, args: list[str]) -> str:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        raise LockError(f"git failed ({' '.join(args)}): {stderr}") from exc
    return result.stdout.strip()


def _read_lock(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
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


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _normalize_ref(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", value):
        return None
    return value


def _equivalent_lock(existing: dict[str, str], new_data: dict[str, str]) -> bool:
    keys = [
        "schema_version",
        "tool.commit",
        "tool.ref",
        "source.esb_repo",
        "source.esb_commit",
        "source.esb_ref",
        "parameters.brand",
    ]
    for key in keys:
        existing_value = existing.get(key) or None
        if existing_value != (new_data.get(key) or None):
            return False
    return True


def _render_lock(
    *,
    schema_version: int,
    locked_at: str,
    tool_commit: str,
    tool_ref: str | None,
    esb_repo: str,
    esb_commit: str,
    esb_ref: str | None,
    brand: str,
) -> str:
    lines = [
        f"schema_version: {schema_version}",
        f"locked_at: \"{locked_at}\"",
        "",
        "tool:",
        f"  commit: \"{tool_commit}\"",
    ]
    if tool_ref:
        lines.append(f"  ref: \"{tool_ref}\"")
    lines.extend(
        [
            "",
            "source:",
            f"  esb_repo: \"{esb_repo}\"",
            f"  esb_commit: \"{esb_commit}\"",
        ]
    )
    if esb_ref:
        lines.append(f"  esb_ref: \"{esb_ref}\"")
    lines.extend(
        [
            "",
            "parameters:",
            f"  brand: \"{brand}\"",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
