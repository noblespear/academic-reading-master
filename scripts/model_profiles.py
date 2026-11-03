#!/usr/bin/env python3
"""User-owned model choices. Reading or saving these never starts a model.

CLI: model_profiles.py [--vault VAULT] check [ROLE ...]
     model_profiles.py [--vault VAULT] get [ROLE]
     model_profiles.py [--vault VAULT] set ROLE --model MODEL --effort EFFORT
     model_profiles.py [--vault VAULT] set ROLE --inherit
The default scope is the user's CODEX_HOME, outside the skill distribution.
Vault fields override user fields. An absent field preserves legacy launcher
overrides; an explicitly empty field inherits the host and removes that override.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from runtime_lock import RuntimeLock

ROLES = ("writing", "drawing", "qa", "translation")
EFFORTS = ("", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
MAX_FILE_BYTES = 8192
MAX_MODEL_LENGTH = 200


def validate_role(role):
    if not isinstance(role, str) or role not in ROLES:
        raise ValueError("role must be writing, drawing, qa or translation")
    return role


def validate_profile(profile):
    if not isinstance(profile, dict) or set(profile) - {"model", "effort"}:
        raise ValueError("a profile accepts only model and effort")
    result = {}
    for key, value in profile.items():
        value = "" if value is None else value
        if not isinstance(value, str):
            raise ValueError(key + " must be a string or null")
        if key == "model":
            if len(value) > MAX_MODEL_LENGTH or any(c.isspace() or not c.isprintable() for c in value):
                raise ValueError("model must be a model ID of at most 200 characters, without whitespace")
        elif value not in EFFORTS:
            raise ValueError("unsupported effort; choose inherit, none, minimal, low, medium, high, xhigh, max or ultra")
        result[key] = value
    return result


def _path(vault=None):
    if vault is not None:
        return Path(vault).resolve() / ".reader" / "model-profiles.json"
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return codex_home.expanduser().resolve() / "academic-reading-master" / "model-profiles.json"


def _load_file(path):
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return {}
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("model profiles file exceeds 8 KiB")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or set(data) != {"version", "profiles"} or type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("invalid model profiles document")
    if not isinstance(data["profiles"], dict):
        raise ValueError("profiles must be an object")
    result = {}
    for role, profile in data["profiles"].items():
        validate_role(role)
        result[role] = validate_profile(profile)
    return result


def load_profiles(vault=None):
    """Bounded user settings, with optional vault overrides merged by field."""
    profiles = _load_file(_path())
    if vault is not None:
        for role, profile in _load_file(_path(vault)).items():
            profiles[role] = {**profiles.get(role, {}), **profile}
    return profiles


def save_profiles(vault, updates):
    """Save only the requested scope; None means user settings. Never run a model."""
    if not isinstance(updates, dict) or not updates or len(updates) > len(ROLES):
        raise ValueError("provide one to four role profiles")
    checked = {validate_role(role): validate_profile(value) for role, value in updates.items()}
    path = _path(vault)
    with RuntimeLock(path.with_suffix(".lock")):
        profiles = _load_file(path)
        for role, profile in checked.items():
            profiles[role] = {**profiles.get(role, {}), **profile}
        payload = json.dumps({"version": 1, "profiles": profiles}, ensure_ascii=False, indent=2).encode("utf-8")
        if len(payload) > MAX_FILE_BYTES:
            raise ValueError("model profiles exceed 8 KiB")
        descriptor, temporary = tempfile.mkstemp(prefix="model-profiles-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return load_profiles(vault)


def get_role_profile(vault, role):
    validate_role(role)
    profiles = load_profiles(vault)
    return {"role": role, "configured": bool(profiles.get(role)), **{key: profiles.get(role, {}).get(key, "") for key in ("model", "effort")}}


def check_profiles(vault=None, roles=ROLES):
    """Report missing choices for only the requested stages; choose nothing."""
    required = list(dict.fromkeys(validate_role(role) for role in roles))
    profiles = load_profiles(vault)
    missing = [role for role in required if not profiles.get(role)]
    return {"version": 1, "configured": not missing, "required_roles": required,
            "missing_roles": missing,
            "profiles": {role: {"configured": bool(profiles.get(role)),
                               "model": profiles.get(role, {}).get("model", ""),
                               "effort": profiles.get(role, {}).get("effort", "")}
                         for role in required}}


def resolve_role_config(vault, role, base_config=None):
    """Preserve transport fields; apply only explicitly saved model/effort keys."""
    validate_role(role)
    result = dict(base_config or {})
    for key, value in load_profiles(vault).get(role, {}).items():
        if value:
            result[key] = value
        else:
            result.pop(key, None)
    return result


def model_choice(config):
    return {key: config.get(key) or None for key in ("model", "effort")}


def route_fingerprint(config):
    """Only route identity, never credentials or entire transport configuration."""
    route = {"role": "qa", "adapter": config.get("adapter", "codex"), **model_choice(config)}
    return hashlib.sha256(json.dumps(route, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read/save per-role model settings without starting a model")
    parser.add_argument("--vault", help="read user settings plus vault overrides; set writes only this vault")
    commands = parser.add_subparsers(dest="command", required=True)
    read = commands.add_parser("get", help="bounded JSON for all roles or one role")
    read.add_argument("role", nargs="?", choices=ROLES)
    check = commands.add_parser("check", help="report choices the agent must ask for; never pick defaults")
    check.add_argument("roles", nargs="*", choices=ROLES)
    write = commands.add_parser("set", help="save one role; empty values inherit the host")
    write.add_argument("role", choices=ROLES)
    write.add_argument("--model")
    write.add_argument("--effort", choices=EFFORTS)
    write.add_argument("--inherit", action="store_true", help="clear both overrides for this role")
    args = parser.parse_args(argv)
    try:
        if args.command == "set":
            if args.inherit and (args.model is not None or args.effort is not None):
                parser.error("--inherit cannot be combined with --model/--effort")
            update = {"model": "", "effort": ""} if args.inherit else {key: getattr(args, key) for key in ("model", "effort") if getattr(args, key) is not None}
            if not update:
                parser.error("set requires --model, --effort or --inherit")
            save_profiles(args.vault, {args.role: update})
        if args.command == "check":
            output = check_profiles(args.vault, args.roles or ROLES)
        elif args.role:
            output = get_role_profile(args.vault, args.role)
        else:
            output = check_profiles(args.vault)
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    sys.exit(main())
