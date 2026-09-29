#!/usr/bin/env python3
"""Render a credential-free Mihomo rule bundle from native Clash providers.

This renderer deliberately does not parse a generated Shadowrocket config.  The
service map below points directly at blackmatrix7's Clash YAML files, while the
small local domain overrides are read from their shared source JSON.  A gateway
assembler can combine the resulting bundle with private proxies and groups.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
CUSTOM_RULES_PATH = ROOT / "src" / "custom-rules.json"
DEFAULT_OUT = ROOT / "dist" / "mihomo" / "bundle.json"
UPSTREAM_REPOSITORY = "blackmatrix7/ios_rule_script"
UPSTREAM_REVISION_API = (
    "https://api.github.com/repos/blackmatrix7/ios_rule_script/commits/master"
)
UPSTREAM_RAW_BASE = "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script"
RETRIES = 3
TIMEOUT = 40
INTERVAL = 86400
UA = "shadowrocket-config-mihomo-build/1.0"


class Failure(Exception):
    pass


# (provider name, upstream category, filename, gateway policy)
# The few *_Classical files are intentional: their plain Category.yaml siblings
# omit the separately generated domain payload and are not complete standalone
# providers.  ChinaMax excludes IPv6 because this gateway profile is IPv4-only.
PROVIDER_SPECS = (
    ("lan", "Lan", "Lan.yaml", "DIRECT"),
    ("openai", "OpenAI", "OpenAI.yaml", "AI"),
    ("gemini", "Gemini", "Gemini.yaml", "AI"),
    ("claude", "Claude", "Claude.yaml", "AI"),
    ("copilot", "Copilot", "Copilot.yaml", "AI"),
    ("youtube", "YouTube", "YouTube.yaml", "YouTube"),
    ("netflix", "Netflix", "Netflix_Classical.yaml", "Netflix"),
    ("disney", "Disney", "Disney.yaml", "Disney+"),
    ("max", "HBO", "HBO.yaml", "Max"),
    ("spotify", "Spotify", "Spotify.yaml", "Spotify"),
    ("telegram", "Telegram", "Telegram.yaml", "Telegram"),
    ("paypal", "PayPal", "PayPal.yaml", "PayPal"),
    ("twitter", "Twitter", "Twitter.yaml", "Twitter"),
    ("facebook", "Facebook", "Facebook.yaml", "Facebook"),
    ("amazon", "Amazon", "Amazon.yaml", "Amazon"),
    ("sony", "Sony", "Sony.yaml", "Game"),
    ("nintendo", "Nintendo", "Nintendo.yaml", "Game"),
    ("epic", "Epic", "Epic.yaml", "Game"),
    ("steam-cn", "SteamCN", "SteamCN.yaml", "Game"),
    ("steam", "Steam", "Steam.yaml", "Game"),
    ("game", "Game", "Game.yaml", "Game"),
    ("github", "GitHub", "GitHub.yaml", "PROXY"),
    ("microsoft", "Microsoft", "Microsoft.yaml", "Microsoft"),
    ("google", "Google", "Google.yaml", "Google"),
    ("apple", "Apple", "Apple_Classical.yaml", "Apple"),
    ("bilibili", "BiliBili", "BiliBili.yaml", "BiliBili"),
    ("netease-music", "NetEaseMusic", "NetEaseMusic.yaml", "DIRECT"),
    ("baidu", "Baidu", "Baidu.yaml", "DIRECT"),
    ("douban", "DouBan", "DouBan.yaml", "DIRECT"),
    ("wechat", "WeChat", "WeChat.yaml", "DIRECT"),
    ("sina", "Sina", "Sina.yaml", "DIRECT"),
    ("zhihu", "Zhihu", "Zhihu.yaml", "DIRECT"),
    ("xiaohongshu", "XiaoHongShu", "XiaoHongShu.yaml", "DIRECT"),
    ("douyin", "DouYin", "DouYin.yaml", "DIRECT"),
    ("tiktok", "TikTok", "TikTok.yaml", "TikTok"),
    ("china", "ChinaMax", "ChinaMax_Classical_No_IPv6.yaml", "DIRECT"),
    ("global", "Global", "Global_Classical.yaml", "PROXY"),
)

CUSTOM_TYPES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-WILDCARD"}
CUSTOM_POLICY_MAP = {
    "AI": "AI",
    "稳定": "AI",
    "速度": "PROXY",
    "PROXY": "PROXY",
    "DIRECT": "DIRECT",
}
PROTECTED_POLICIES = (
    "AI", "Google", "YouTube", "Netflix", "Disney+", "Max", "Telegram",
    "Twitter", "Facebook", "TikTok", "PROXY",
)
REQUIRED_POLICIES = (
    "AI", "YouTube", "Netflix", "Disney+", "Max", "Spotify", "Telegram",
    "PayPal", "Twitter", "Facebook", "Amazon", "Game", "PROXY", "Microsoft",
    "Google", "Apple", "BiliBili", "TikTok",
)


def fetch(url: str) -> bytes:
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            headers = {"User-Agent": UA}
            if url.startswith("https://api.github.com/"):
                headers["Accept"] = "application/vnd.github+json"
                token = os.environ.get("GH_TOKEN")
                if token:
                    headers["Authorization"] = "Bearer " + token
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                if response.status != 200:
                    raise Failure(f"HTTP {response.status}")
                body = response.read()
            if not body.strip():
                raise Failure("empty response")
            return body
        except Exception as exc:
            last = exc
            if attempt < RETRIES:
                time.sleep(2 * attempt)
    raise Failure(f"failed to fetch {url}: {type(last).__name__}: {last}")


def validate_revision(revision: str) -> str:
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise Failure("blackmatrix7 upstream revision is not a 40-character commit")
    return revision


def resolve_upstream_commit(fetcher=fetch) -> str:
    try:
        document = json.loads(fetcher(UPSTREAM_REVISION_API).decode("utf-8", "strict"))
    except (AttributeError, UnicodeError, ValueError) as exc:
        raise Failure(f"cannot resolve blackmatrix7 master commit: {type(exc).__name__}") from None
    if not isinstance(document, dict):
        raise Failure("cannot resolve blackmatrix7 master commit: invalid response")
    return validate_revision(document.get("sha"))


def validate_native_payload(body: bytes, name: str, policy: str) -> int:
    """Validate the entire simple YAML format emitted by these Clash providers."""
    if not isinstance(body, bytes):
        raise Failure(f"{name}: provider response must be raw bytes")
    try:
        text = body.decode("utf-8", "strict")
    except UnicodeError:
        raise Failure(f"{name}: native Clash YAML is not valid UTF-8") from None
    # The pinned upstream files use comments, one top-level payload key and
    # plain scalar list items.  Reject every other line, including a broken
    # trailing YAML key, instead of validating only the payload prefix.
    in_payload = False
    items = []
    for line in text.splitlines():
        if not in_payload:
            if not line or line.startswith("#"):
                continue
            if line == "payload:":
                in_payload = True
                continue
            raise Failure(f"{name}: invalid native Clash YAML header")
        if not line:
            continue
        if line.startswith("  - ") and line[4:].split(",", 1)[0].upper() in {"MATCH", "FINAL"}:
            raise Failure(f"{name}: provider for {policy} contains a catch-all rule")
        match = re.fullmatch(r"  - ([A-Z][A-Z0-9-]*,[^\s#\[\]{}|>@`;'\"\\]+)", line)
        if not match:
            raise Failure(f"{name}: unsupported or invalid native Clash YAML payload")
        items.append(match.group(1))
    if not in_payload:
        raise Failure(f"{name}: native Clash YAML is missing a single payload key")
    if not items:
        raise Failure(f"{name}: native Clash payload is empty")
    return len(items)


def provider_entries(revision: str) -> list[dict]:
    revision = validate_revision(revision)
    base = f"{UPSTREAM_RAW_BASE}/{revision}/rule/Clash"
    entries = []
    for name, category, filename, policy in PROVIDER_SPECS:
        entries.append({
            "name": name,
            "type": "http",
            "behavior": "classical",
            "format": "yaml",
            "url": f"{base}/{category}/{filename}",
            "path": f"ruleset/{filename}",
            "interval": INTERVAL,
            "policy": policy,
        })
    return entries


def validate_provider_contract(providers: list[dict], revision: str,
                               require_digest: bool = False) -> None:
    revision = validate_revision(revision)
    names = set()
    paths = set()
    expected_prefix = f"{UPSTREAM_RAW_BASE}/{revision}/rule/Clash/"
    for provider in providers:
        name = provider["name"]
        path = PurePosixPath(provider["path"])
        if name in names or provider["path"] in paths:
            raise Failure("duplicate Mihomo provider name or path")
        if not re.fullmatch(r"[a-z0-9-]+", name):
            raise Failure(f"unsafe Mihomo provider name: {name}")
        if (path.is_absolute() or ".." in path.parts or path.parts[0] != "ruleset"
                or path.suffix != ".yaml"):
            raise Failure(f"unsafe Mihomo provider path: {path}")
        if (provider["type"], provider["behavior"], provider["format"], provider["interval"]) != (
                "http", "classical", "yaml", INTERVAL):
            raise Failure(f"invalid Mihomo provider contract: {name}")
        if not provider["url"].startswith(expected_prefix) or not provider["url"].endswith(".yaml"):
            raise Failure(f"provider is not a native blackmatrix7 Clash YAML: {name}")
        digest = provider.get("sha256")
        if require_digest and (not isinstance(digest, str)
                               or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise Failure(f"provider is missing a validated SHA-256 digest: {name}")
        names.add(name)
        paths.add(provider["path"])


def load_custom_rules(path: Path = CUSTOM_RULES_PATH) -> list[str]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Failure(f"cannot read shared custom rules: {type(exc).__name__}") from None
    if not isinstance(document, list):
        raise Failure("shared custom rules must be an array")

    result = []
    seen = set()
    for index, entry in enumerate(document, 1):
        allowed_fields = {"rule", "note", "modes", "audiences"}
        if (not isinstance(entry, dict) or set(entry) - allowed_fields
                or not isinstance(entry.get("rule"), str)
                or not isinstance(entry.get("note"), str) or not entry["note"].strip()):
            raise Failure(f"custom rule {index}: invalid record")
        # The gateway artifact represents the existing personal fallback target.
        if "personal" not in entry.get("audiences", ["generic", "personal"]):
            continue
        if "fallback" not in entry.get("modes", ["select", "fallback", "hybrid", "stable"]):
            continue
        parts = [part.strip() for part in entry["rule"].split(",")]
        if len(parts) != 3 or parts[0].upper() not in CUSTOM_TYPES:
            raise Failure(f"custom rule {index}: unsupported Mihomo rule")
        rule_type, value, source_policy = parts[0].upper(), parts[1].lower(), parts[2]
        if (not value or len(value) > 253 or not re.fullmatch(r"[a-z0-9_.?*\-]+", value)
                or source_policy not in CUSTOM_POLICY_MAP):
            raise Failure(f"custom rule {index}: unsupported policy or empty value")
        if rule_type in {"DOMAIN", "DOMAIN-SUFFIX"} and any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in value.split(".")):
            raise Failure(f"custom rule {index}: invalid domain value")
        rendered = f"{rule_type},{value},{CUSTOM_POLICY_MAP[source_policy]}"
        condition = (rule_type, value)
        if condition in seen:
            raise Failure(f"custom rule {index}: duplicate condition in gateway profile")
        seen.add(condition)
        result.append(rendered)
    return result


def render_bundle(fetcher=fetch, revision: str | None = None,
                  revision_resolver=resolve_upstream_commit) -> dict:
    revision = validate_revision(revision) if revision is not None else revision_resolver()
    providers = provider_entries(revision)
    validate_provider_contract(providers, revision)
    counts = {}
    for provider in providers:
        body = fetcher(provider["url"])
        counts[provider["name"]] = validate_native_payload(
            body, provider["name"], provider["policy"])
        provider["sha256"] = hashlib.sha256(body).hexdigest()
    validate_provider_contract(providers, revision, require_digest=True)

    custom_rules = load_custom_rules()
    rules = [*custom_rules]
    for provider in providers:
        # Mihomo evaluates rules strictly top-to-bottom.  Keep the CN database
        # fallback ahead of the broad Global provider as well as MATCH.
        if provider["name"] == "global":
            rules.append("GEOIP,CN,DIRECT")
        rules.append(f"RULE-SET,{provider['name']},{provider['policy']}")
    rules.append("MATCH,PROXY")
    return {
        "schema_version": 1,
        "kind": "mihomo-rule-bundle",
        "profile": "personal-fallback-ipv4",
        "upstream_repository": UPSTREAM_REPOSITORY,
        "upstream_commit": revision,
        "custom_rules_source": "src/custom-rules.json",
        "policy_contract": {
            "required": list(REQUIRED_POLICIES),
            "protected": list(PROTECTED_POLICIES),
            "missing_protected_policy": "REJECT",
            "terminal_rule": "MATCH,PROXY",
        },
        "rule_providers": providers,
        "rules": rules,
        "validation": {"payload_item_counts": counts},
    }


def write_bundle(bundle: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(bundle, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=output.name + ".", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    try:
        bundle = render_bundle()
        write_bundle(bundle, args.out)
    except Failure as exc:
        print(f"Mihomo bundle failed: {exc}")
        return 1
    counts = bundle["validation"]["payload_item_counts"]
    print(f"Mihomo bundle: {len(bundle['rule_providers'])} providers, "
          f"{sum(counts.values())} validated payload items, "
          f"{len(bundle['rules'])} ordered rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
