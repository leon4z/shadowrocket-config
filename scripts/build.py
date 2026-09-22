#!/usr/bin/env python3
"""拉上游 lazy_group.conf，生成通用与个人两组三种配置。

流程：
  1. 拉上游 johnshall/lazy_group.conf
  2. 检查锚点 —— 上游改了结构就立刻失败，不做基于模式的瞎替换
  3. 每个变体各生成一份：套用覆盖规格 → 校验 → 写 dist/
  4. 把配置里所有远程规则集拉下来逐条校验（类型名、空文件、IP 规则的 no-resolve）
  5. 检查 [Rule] 引用的策略名是否真的在 [Proxy Group] 里定义过

校验失败就不产出、不发布，release 分支停在上一版。
输出是 (上游内容, 规格) 的纯函数 —— 同样的输入逐字节产出同样的结果，
上游或规格没变就不产生新提交。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_PATH = os.path.join(ROOT, "src", "overrides.py")
SELECTION_PATH = os.path.join(ROOT, "src", "selection.json")

# 小火箭规则行白名单。前 16 个来自主二进制里那条校验正则；
# IP6-CIDR / PROTOCOL / AND / NOT / OR 是同一套解析器的其它 token。
RULE_TYPES = {
    "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-WILDCARD", "DOMAIN",
    "HOST", "HOST-SUFFIX", "HOST-KEYWORD", "HOST-WILDCARD",
    "IP-CIDR", "IP6-CIDR", "IP-ASN", "URL-REGEX", "GEOIP",
    "FINAL", "USER-AGENT", "RULE-SET", "DOMAIN-SET", "SCRIPT", "DST-PORT",
    "PROTOCOL", "AND", "NOT", "OR",
}
IP_TYPES = {"IP-CIDR", "IP6-CIDR", "IP-ASN", "GEOIP"}
GROUP_TYPES = {"select", "url-test", "fallback", "load-balance", "random"}

BUILTIN_POLICIES = {
    "DIRECT", "PROXY", "TAILSCALE", "REJECT", "REJECT-DICT", "REJECT-ARRAY",
    "REJECT-200", "REJECT-IMG", "REJECT-TINYGIF", "REJECT-VIDEO",
    "REJECT-DROP", "REJECT-NO-DROP",
}

RETRIES = 3
TIMEOUT = 30
UA = "shadowrocket-config-build/1.0"


class Failure(Exception):
    pass


def load_spec():
    spec = importlib.util.spec_from_file_location("overrides", SPEC_PATH)
    if spec is None or spec.loader is None:
        raise Failure(f"无法加载规格文件 {SPEC_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def validate_selection(document: dict, spec) -> dict:
    """Reject broad filters, malformed metadata and accidental private fields."""
    if not isinstance(document, dict) or set(document) != {
        "version", "mode", "generated_at", "window_start", "window_end",
        "completed_runs", "selection_id", "groups",
    }:
        raise Failure("采样清单顶层字段不符合固定契约")
    if document["version"] != 1 or document["mode"] != "trial":
        raise Failure("采样清单版本或模式不支持")
    times = [document[k] for k in ("window_start", "window_end", "generated_at")]
    if any(type(v) is not int or v <= 0 for v in times) or times != sorted(times):
        raise Failure("采样清单时间窗口无效")
    if type(document["completed_runs"]) is not int or document["completed_runs"] < 3:
        raise Failure("采样清单完成轮数不足 3")
    if not isinstance(document["selection_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", document["selection_id"]):
        raise Failure("采样清单 selection_id 无效")
    groups = document["groups"]
    if not isinstance(groups, dict) or not spec.REQUIRED_SAMPLED_GROUPS <= set(groups):
        raise Failure("采样清单缺少速度或既有国家组")
    if len(groups) > 32:
        raise Failure("采样清单分组数量超限")
    for name, entry in groups.items():
        if not isinstance(name, str) or (name != "速度" and not re.fullmatch(r"[\w\u4e00-\u9fff]{1,20}节点", name)):
            raise Failure("采样清单分组名无效")
        if name in {"稳定", "PROXY", "DIRECT"} or not isinstance(entry, dict) or set(entry) != {"pattern", "node_count"}:
            raise Failure("采样清单分组字段无效")
        pattern = entry["pattern"]
        if type(entry["node_count"]) is not int or entry["node_count"] < 1 or entry["node_count"] > 1000:
            raise Failure(f"{name} 的 node_count 无效")
        if name == "速度" and entry["node_count"] > spec.PERSONAL_SPEED_LIMIT:
            raise Failure("个人速度组超过 10 个节点")
        if not isinstance(pattern, str) or len(pattern) > 20000 or not pattern.startswith("(?i)^(?:") or not pattern.endswith(")$"):
            raise Failure(f"{name} 必须使用精确锚定正则")
        if any(c in pattern for c in (",", "\n", "\r")):
            raise Failure(f"{name} 正则含分隔符或换行")
        inner = pattern[len("(?i)^(?:"):-2]
        if not inner:
            raise Failure(f"{name} 正则为空")
        alias_len = 0
        index = 0
        while index < len(inner):
            char = inner[index]
            if char == "\\":
                if inner[index:index + 4] in {"\\x2c", "\\x23"}:
                    index += 4
                elif index + 1 < len(inner) and not inner[index + 1].isalnum():
                    index += 2
                else:
                    raise Failure(f"{name} 正则含非字面量转义")
                alias_len += 1
                continue
            if char == "|":
                if alias_len == 0:
                    raise Failure(f"{name} 正则含空节点名")
                alias_len = 0
            elif char in ".^$*+?{}[]()":
                raise Failure(f"{name} 正则含宽泛匹配符")
            else:
                alias_len += 1
            index += 1
        if alias_len == 0:
            raise Failure(f"{name} 正则含空节点名")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise Failure(f"{name} 正则无法编译: {exc}") from exc
    return document


def load_selection(spec, path: str = SELECTION_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as file:
            document = json.load(file)
    except (OSError, ValueError) as exc:
        raise Failure("缺少或无法解析 src/selection.json，不能退回全节点筛选") from exc
    return validate_selection(document, spec)


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #

def fetch(url: str) -> str:
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                if resp.status != 200:
                    raise Failure(f"HTTP {resp.status}")
                body = resp.read().decode("utf-8", "replace")
            if not body.strip():
                raise Failure("内容为空")
            return body
        except (urllib.error.URLError, urllib.error.HTTPError, Failure, OSError) as exc:
            last = exc
            if attempt < RETRIES:
                time.sleep(2 * attempt)
    raise Failure(f"{type(last).__name__}: {last}")


# --------------------------------------------------------------------------- #
# 覆盖变换
# --------------------------------------------------------------------------- #

def split_params(line: str) -> tuple[str, list[str]]:
    name, _, rest = line.partition("=")
    return name.strip(), [p.strip() for p in rest.split(",")]


def join_params(name: str, params: list[str]) -> str:
    return f"{name} = " + ",".join(params)


def sampled_group(name: str, pattern: str, spec) -> str:
    kind = "fallback" if name == "速度" else "url-test"
    parts = [kind, f"policy-regex-filter={pattern}", f"interval={spec.PROBE_INTERVAL}",
             f"timeout={spec.PROBE_TIMEOUT}", f"url={spec.PROBE_URL}"]
    if kind == "url-test":
        parts.append(f"tolerance={spec.TOLERANCE}")
    return join_params(name, parts)


def group_patterns(upstream: str, spec, variant: dict, selection: dict | None) -> dict[str, str]:
    if variant["audience"] == "personal":
        if selection is None:
            raise Failure("个人版缺少采样清单")
        return {name: entry["pattern"] for name, entry in selection["groups"].items()}
    # 通用版仅取上游的地区匹配；速度池直接匹配这些节点，不嵌套自动组。
    patterns = {}
    for line in effective(parse_sections(upstream.splitlines()).get("proxy group", [])):
        name, params = split_params(line)
        if name.endswith("节点") and params[0] == "url-test":
            pattern = next((p.split("=", 1)[1] for p in params if p.startswith("policy-regex-filter=")), None)
            if not pattern:
                raise Failure(f"上游地区组 {name} 缺少筛选条件")
            patterns[name] = pattern
    if not (spec.REQUIRED_SAMPLED_GROUPS - {"速度"}) <= patterns.keys():
        raise Failure("上游缺少通用版所需的地区组")
    patterns["速度"] = "|".join(f"(?:{pattern})" for pattern in patterns.values())
    return patterns


def stable_pattern(spec, variant: dict) -> str:
    return spec.STABLE_PATTERN if variant["audience"] == "personal" else spec.GENERIC_STABLE_PATTERN


def extra_groups(spec, variant: dict, patterns: dict[str, str]) -> list[str]:
    lines = []
    if variant["default_policy"] == "速度":
        lines.append(sampled_group("速度", patterns["速度"], spec))
    if variant["strict_stable"]:
        lines.append(join_params("稳定", ["fallback", f"policy-regex-filter={stable_pattern(spec, variant)}",
                                          f"interval={spec.PROBE_INTERVAL}", f"timeout={spec.PROBE_TIMEOUT}",
                                          f"url={spec.PROBE_URL}"]))
    return lines


def tune_group(line: str, spec, variant: dict, patterns: dict[str, str]) -> str:
    """Apply measured exact filters and variant routing to upstream groups."""
    name, params = split_params(line)

    if name in patterns:
        return sampled_group(name, patterns[name], spec)
    if variant["audience"] == "personal" and name.endswith("节点"):
        raise Failure(f"上游新增地区组 {name} 尚无个人采样依据")
    if name in spec.STABLE_ONLY_SERVICES and variant["strict_stable"]:
        return join_params(name, ["select", "稳定"])

    params = [p for p in params
              if not any(p.startswith(f"{d}=") for d in spec.DROP_GROUP_PARAMS)]

    params = [f"tolerance={spec.TOLERANCE}" if p.startswith("tolerance=") else p
              for p in params]

    params = [variant["default_policy"] if p == "PROXY" else p for p in params]

    return join_params(name, params)


def rewrite_url(url: str, spec) -> str:
    """把规则集 URL 换到指定的分发通道（见 spec.USE_JSDELIVR_FOR_RULESETS）。

    通用改写而不是逐个列白名单：raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>
    → cdn.jsdelivr.net/gh/<owner>/<repo>@<ref>/<path>
    这样上游以后引用新的 raw 仓库也能自动接住。
    """
    if not getattr(spec, "USE_JSDELIVR_FOR_RULESETS", False):
        return url
    if not url.startswith(spec._RAW_BASE):
        return url
    rest = url[len(spec._RAW_BASE):]
    parts = rest.split("/", 3)
    if len(parts) < 4:
        return url
    owner, repo, ref, path = parts
    return f"{spec._JSD_BASE}{owner}/{repo}@{ref}/{path}"


def expand_rule(line: str, spec, variant: dict) -> list[str]:
    """[Rule] 行：方言替换 + 策略替换。返回若干行（方言替换会一行变多行）。"""
    params = [p.strip() for p in line.split(",")]
    rtype = params[0].upper()

    dialect = {d["qx"]: d for d in spec.RULESET_DIALECT}
    if rtype == "RULE-SET" and len(params) >= 3 and params[1] in dialect:
        d = dialect[params[1]]
        policy = substitute_policy(params[2], None, spec, variant)
        out = [f"RULE-SET,{rewrite_url(d['sr'], spec)},{policy}"]
        if d["sr_domain_set"]:
            out.append(f"DOMAIN-SET,{rewrite_url(d['sr_domain_set'], spec)},{policy}")
        out.extend(f"DOMAIN-WILDCARD,{w},{policy}" for w in d["wildcards"])
        return out

    if rtype in {"RULE-SET", "DOMAIN-SET"} and len(params) >= 3:
        params[1] = rewrite_url(params[1], spec)
        params[2] = substitute_policy(params[2], None, spec, variant)
    elif rtype == "FINAL" and len(params) >= 2 and params[1]:
        params[1] = substitute_policy(params[1], None, spec, variant)
    elif len(params) >= 3:
        params[-1] = substitute_policy(params[-1], None, spec, variant)

    return [",".join(params)]


def substitute_policy(policy: str, group_name, spec, variant: dict) -> str:
    return variant["default_policy"] if policy == "PROXY" else policy


def general_overrides(spec) -> dict[str, str]:
    overrides = dict(getattr(spec, "GENERAL_OVERRIDES", None) or {})
    return {k: v for k, v in overrides.items() if k.lower() != "update-url"}


def transform(upstream: str, spec, variant: dict, selection: dict | None) -> list[str]:
    patterns = group_patterns(upstream, spec, variant, selection)
    upstream_groups = [split_params(line)[0] for line in effective(
        parse_sections(upstream.splitlines()).get("proxy group", []))]
    countries = [name for name in upstream_groups if name in patterns and name != "速度"]
    if not countries:
        raise Failure("上游缺少地区分组，无法放置新增地区组")
    new_countries = sorted(set(patterns) - set(upstream_groups) - {"速度"})
    overrides = general_overrides(spec)
    applied: set[str] = set()

    def pending_general() -> list[str]:
        """[General] 段里还没被改写的覆盖项，追加到段末。"""
        return [f"{k} = {v}" for k, v in overrides.items() if k not in applied]

    out: list[str] = []
    section = None
    for raw in upstream.split("\n"):
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            if section == "general":
                out.extend(pending_general())
                applied.update(overrides)
            section = s[1:-1].strip().lower()
            out.append(raw)
            if section == "proxy group":
                out.append("")
                if variant["audience"] == "personal":
                    out.append("# 个人采样快照筛选。")
                elif variant["strict_stable"]:
                    out.append("# 通用地区匹配；稳定组默认空，请在使用前指定节点并保存本地配置。")
                else:
                    out.append("# 通用地区匹配；地区组按延迟自动选择节点。")
                out.extend(extra_groups(spec, variant, patterns))
            continue
        if s and not s.startswith("#"):
            if section == "general":
                key = s.split("=", 1)[0].strip()
                if key.lower() == "update-url":
                    continue
                if key in overrides:
                    out.append(f"{key} = {overrides[key]}")
                    applied.add(key)
                    continue
            if section == "proxy group":
                out.append(tune_group(raw, spec, variant, patterns))
                if split_params(raw)[0] == countries[-1]:
                    out.extend(sampled_group(name, patterns[name], spec) for name in new_countries)
                continue
            if section == "rule":
                out.extend(expand_rule(raw, spec, variant))
                continue
        out.append(raw)
    if section == "general":
        out.extend(pending_general())
    return out


# --------------------------------------------------------------------------- #
# 解析与校验
# --------------------------------------------------------------------------- #

def parse_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    cur = None
    for raw in lines:
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            cur = line[1:-1].strip().lower()
            sections.setdefault(cur, [])
            continue
        if cur is not None:
            sections[cur].append(raw)
    return sections


def effective(lines: list[str]) -> list[str]:
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def validate_groups(sections: dict[str, list[str]]) -> set[str]:
    names: set[str] = set()
    references: dict[str, list[str]] = {}
    for line in effective(sections.get("proxy group", [])):
        name, params = split_params(line)
        if not name:
            raise Failure(f"分组行没有名字: {line[:80]!r}")
        if name in names:
            raise Failure(f"分组名重复: {name}")
        names.add(name)

        gtype = params[0].lower() if params else ""
        if gtype not in GROUP_TYPES:
            raise Failure(f"分组 {name} 的类型不认识: {gtype!r}")

        filt = next((p for p in params if p.startswith("policy-regex-filter=")), None)
        inline = [p for p in params[1:] if "=" not in p]
        references[name] = inline
        if filt:
            try:
                re.compile(filt.split("=", 1)[1])
            except re.error as exc:
                raise Failure(f"分组 {name} 的 policy-regex-filter 不是合法正则: {exc}")
        elif not inline:
            raise Failure(f"分组 {name} 既没有成员也没有 policy-regex-filter")
    known = {name.upper(): name for name in names}
    for name, members in references.items():
        for member in members:
            if member.upper() not in known and member.upper() not in BUILTIN_POLICIES:
                raise Failure(f"分组 {name} 引用未定义的成员 {member!r}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(name: str) -> None:
        if name in visiting:
            raise Failure(f"分组引用形成循环: {name}")
        if name in visited:
            return
        visiting.add(name)
        for member in references[name]:
            target = known.get(member.upper())
            if target:
                walk(target)
        visiting.remove(name)
        visited.add(name)

    for name in references:
        walk(name)
    return names


def validate_variant(sections: dict[str, list[str]], spec, variant: dict,
                     selection: dict | None, upstream: str) -> set[str]:
    if effective(sections.get("proxy", [])):
        raise Failure("[Proxy] 含实际节点，公开配置必须为空")
    groups = validate_groups(sections)
    definitions = dict(split_params(line) for line in effective(sections.get("proxy group", [])))
    patterns = group_patterns(upstream, spec, variant, selection)
    if variant["default_policy"] != "速度":
        patterns.pop("速度")
        if "速度" in groups:
            raise Failure(f"{variant['id']}: 不应包含速度组")
    expected_groups = set(patterns)
    if variant["strict_stable"]:
        expected_groups.add("稳定")
    elif "稳定" in groups:
        raise Failure(f"{variant['id']}: 不应包含稳定组")
    if not expected_groups <= groups:
        raise Failure(f"{variant['id']}: 精选组缺失")
    country_positions = [i for i, name in enumerate(definitions) if name in patterns and name != "速度"]
    if country_positions != list(range(country_positions[0], country_positions[0] + len(country_positions))):
        raise Failure(f"{variant['id']}: 地区分组必须连续排列")
    for name, pattern in patterns.items():
        line = definitions[name]
        want_type = "fallback" if name == "速度" else "url-test"
        if line[0] != want_type or f"policy-regex-filter={pattern}" not in line:
            raise Failure(f"{variant['id']}: {name} 未应用对应筛选正则")
        expected = {f"url={spec.PROBE_URL}", f"timeout={spec.PROBE_TIMEOUT}",
                    f"interval={spec.PROBE_INTERVAL}"}
        if not expected <= set(line):
            raise Failure(f"{variant['id']}: {name} 探测参数不一致")
        if (name == "速度" and any(p.startswith("tolerance=") for p in line)) or (
            name != "速度" and f"tolerance={spec.TOLERANCE}" not in line):
            raise Failure(f"{variant['id']}: {name} tolerance 无效")
    if variant["strict_stable"]:
        stable = definitions["稳定"]
        if stable != ["fallback", f"policy-regex-filter={stable_pattern(spec, variant)}",
                      f"interval={spec.PROBE_INTERVAL}", f"timeout={spec.PROBE_TIMEOUT}", f"url={spec.PROBE_URL}"]:
            raise Failure(f"{variant['id']}: 稳定组定义不符")
    for anchor in spec.ANCHORS:
        if " = select," not in anchor:
            continue
        name = anchor.split(" = ", 1)[0]
        params = definitions.get(name)
        if not params or params[0] != "select":
            raise Failure(f"{variant['id']}: 服务组 {name} 缺失或类型变更")
        inline = [p for p in params[1:] if "=" not in p]
        if name in spec.STABLE_ONLY_SERVICES and variant["strict_stable"]:
            if inline != ["稳定"]:
                raise Failure(f"{variant['id']}: {name} 必须严格仅选稳定")
        elif variant["default_policy"] not in inline:
            raise Failure(f"{variant['id']}: {name} 默认代理出口错误")
        if ",DIRECT," in anchor and "DIRECT" not in inline:
            raise Failure(f"{variant['id']}: {name} 原有直连选项丢失")
        if variant["default_policy"] == "速度" and "PROXY" in inline:
            raise Failure(f"{variant['id']}: {name} 残留手动 PROXY")
    finals = [line.split(",", 2)[1] for line in effective(sections.get("rule", [])) if line.startswith("FINAL,")]
    if finals != [variant["default_policy"]]:
        raise Failure(f"{variant['id']}: FINAL 出口与变体不符")
    update_urls = [line for line in effective(sections.get("general", []))
                   if line.split("=", 1)[0].strip().lower() == "update-url"]
    if update_urls:
        raise Failure(f"{variant['id']}: 发布配置不得包含 update-url")
    return groups


def validate_rules(sections: dict[str, list[str]], groups: set[str]) -> list[tuple[str, str]]:
    """返回 [(类型, URL)]，并检查策略名。

    策略名按不区分大小写比对：上游 [Rule] 写 `YOUTUBE`、[Proxy Group] 定义
    `YouTube`，这套写法在实际使用中有效，说明小火箭的策略名查找不区分大小写。
    """
    known = {g.upper() for g in groups} | BUILTIN_POLICIES
    refs: list[tuple[str, str]] = []
    for line in effective(sections.get("rule", [])):
        parts = [p.strip() for p in line.split(",")]
        rtype = parts[0].upper()
        if rtype not in RULE_TYPES:
            raise Failure(f"[Rule] 里类型无法识别: {line[:80]!r}")

        if rtype in {"RULE-SET", "DOMAIN-SET"}:
            if len(parts) < 3:
                raise Failure(f"{rtype} 缺少策略列: {line[:80]!r}")
            refs.append((rtype, parts[1]))
            policy = parts[2]
        elif rtype == "FINAL":
            policy = parts[1] if len(parts) > 1 and parts[1] else None
        else:
            tail = [p for p in parts[2:] if p and p not in {"no-resolve", "force-remote-dns"}]
            policy = tail[-1] if tail else None

        if policy and policy.upper() not in known:
            raise Failure(f"[Rule] 引用了未定义的策略 {policy!r}: {line[:80]!r}")
    return refs


def check_ruleset(body: str) -> dict:
    stats = {"rules": 0, "ip": 0, "ip_no_resolve": 0}
    for lineno, raw in enumerate(body.split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        rtype = parts[0].upper()
        if rtype not in RULE_TYPES:
            raise Failure(f"第 {lineno} 行类型无法识别: {line[:80]!r}")
        if len(parts) < 2 or not parts[1]:
            raise Failure(f"第 {lineno} 行缺少匹配值: {line[:80]!r}")
        stats["rules"] += 1
        if rtype in IP_TYPES:
            stats["ip"] += 1
            if "no-resolve" not in parts:
                stats["ip_no_resolve"] += 1
    if stats["rules"] == 0:
        raise Failure("规则集里一条有效规则都没有")
    return stats


def check_domainset(body: str) -> dict:
    stats = {"domains": 0}
    dom = re.compile(r"^\.?[A-Za-z0-9_*.-]+$")
    for lineno, raw in enumerate(body.split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not dom.match(line.split()[0]):
            raise Failure(f"第 {lineno} 行不像域名: {line[:80]!r}")
        stats["domains"] += 1
    if stats["domains"] == 0:
        raise Failure("域名集里一条都没有")
    return stats


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description="从上游生成小火箭配置")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--report", default=None, help="把校验报告写成 JSON")
    ap.add_argument("--audience", choices=("all", "generic", "personal"), default="all",
                    help="通用版可独立构建，不读取个人采样清单")
    ap.add_argument("--no-network", action="store_true",
                    help="用 dist/upstream-lazy_group.conf 缓存代替联网抓取（离线自检用）")
    args = ap.parse_args()

    spec = load_spec()
    variants = [v for v in spec.VARIANTS if args.audience in ("all", v["audience"])]
    selection = load_selection(spec) if any(v["audience"] == "personal" for v in variants) else None
    os.makedirs(args.out_dir, exist_ok=True)
    # 上游内容的缓存固定放 dist/cache/，与 --out-dir 无关：
    # 这样 --no-network 换输出目录也能用，而且不会被当成产物发布出去。
    cache = os.path.join(ROOT, "dist", "cache", "lazy_group.conf")
    os.makedirs(os.path.dirname(cache), exist_ok=True)

    if args.no_network:
        if not os.path.exists(cache):
            raise SystemExit(f"离线模式需要 {cache}，先联网跑一次")
        upstream = open(cache, encoding="utf-8").read()
        print(f"离线模式：用缓存的上游内容（{len(upstream.splitlines())} 行）")
    else:
        print(f"拉取上游 {spec.UPSTREAM_URL}")
        upstream = fetch(spec.UPSTREAM_URL)
        with open(cache, "w", encoding="utf-8") as fh:
            fh.write(upstream)

    upstream_hash = hashlib.sha256(upstream.encode("utf-8")).hexdigest()[:12]

    # 锚点检查
    missing = [a for a in spec.ANCHORS if a not in upstream]
    if missing:
        print("上游锚点缺失，说明上游改了结构，覆盖规格需要跟着改：", file=sys.stderr)
        for a in missing:
            print(f"  - {a!r}", file=sys.stderr)
        return 1

    m = re.search(r"^#\s*(\d{4}-\d{2}-\d{2})\s*$", upstream, re.M)
    upstream_rev = m.group(1) if m else "未知"
    print(f"上游版本标记 {upstream_rev}，内容 sha256:{upstream_hash}，锚点全部命中")

    report: dict = {
        "upstream_url": spec.UPSTREAM_URL,
        "upstream_rev": upstream_rev,
        "upstream_sha256": upstream_hash,
        "variants": [],
        "refs": [],
        "warnings": [],
        "selection": {key: selection[key] for key in (
            "mode", "selection_id", "generated_at", "window_start", "window_end", "completed_runs")} if selection else None,
    }

    built: dict[str, tuple[str, list[tuple[str, str]]]] = {}

    for variant in variants:
        lines = transform(upstream, spec, variant, selection)
        sections = parse_sections(lines)

        # 生成后的自检：不该再有圈X 引用、不该有残留的 raw 规则集地址；
        # custom 变体不该再有裸 PROXY 策略；[General] 覆盖要真的生效。
        for key, val in general_overrides(spec).items():
            want = f"{key} = {val}"
            if want not in effective(sections.get("general", [])):
                raise Failure(f"{variant['id']}: [General] 覆盖没生效，期望 {want!r}")

        live_rules = effective(sections.get("rule", []))
        leftover = [l for l in live_rules if "/QuantumultX/" in l]
        if leftover:
            raise Failure(f"{variant['id']}: 仍有圈X 引用 {leftover[:2]}")
        if getattr(spec, "USE_JSDELIVR_FOR_RULESETS", False):
            raw = [l for l in live_rules if "raw.githubusercontent.com" in l]
            if raw:
                raise Failure(
                    f"{variant['id']}: 还有规则集地址没换到 jsDelivr（改写规则没覆盖到）: {raw[:2]}")
        groups = validate_variant(sections, spec, variant, selection, upstream)
        refs = validate_rules(sections, groups)

        header = [
            f"# 由 leon4z/shadowrocket-config 生成 · {variant['title']}",
            f"# 上游 {spec.UPSTREAM_URL}",
            f"# 上游版本标记 {upstream_rev} · 上游内容 sha256:{upstream_hash}",
        ]
        if variant["audience"] == "personal":
            header.extend([
                f"# 节点筛选：试跑快照 {selection['selection_id']} · 完成 {selection['completed_runs']} 轮",
                "# 采样窗口 " + datetime.fromtimestamp(selection["window_start"], timezone.utc).isoformat()
                + " 至 " + datetime.fromtimestamp(selection["window_end"], timezone.utc).isoformat(),
                "# 快照生成 " + datetime.fromtimestamp(selection["generated_at"], timezone.utc).isoformat()
                + " · 这不是 24 小时或长期稳定性结论；清单未自动更新。",
            ])
        elif variant["strict_stable"]:
            header.append("# 通用版：不使用个人采样。稳定组默认空，使用自动/混合版前须指定稳定节点。")
        else:
            header.append("# 通用版：不使用个人采样。服务出口默认跟随首页选择，地区组保持自动测速。")
        header.append("# 上游 + 覆盖规格，差异与自定义方法见仓库 README。")
        out_text = "\n".join(header) + "\n" + "\n".join(lines)
        path = os.path.join(args.out_dir, f"{variant['id']}.conf")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(out_text)

        built[variant["id"]] = (out_text, refs)
        report["variants"].append({
            "id": variant["id"],
            "audience": variant["audience"],
            "title": variant["title"],
            "file": os.path.basename(path),
            "groups": len(groups),
            "refs": len(refs),
            "lines": len(out_text.splitlines()),
        })
        print(f"  {variant['id']}.conf  {len(groups)} 组 / {len(refs)} 个远程集合 / "
              f"{len(out_text.splitlines())} 行")

    # 远程集合校验：六个变体共享的集合只抓一遍
    all_refs = sorted({r for _, refs in built.values() for r in refs})
    if not args.no_network:
        print(f"\n校验 {len(all_refs)} 个远程集合")
        failures = []
        for rtype, url in all_refs:
            short = url.split("/rule/", 1)[-1] if "/rule/" in url else url.rsplit("/", 1)[-1]
            try:
                body = fetch(url)
                stats = check_domainset(body) if rtype == "DOMAIN-SET" else check_ruleset(body)
            except Failure as exc:
                failures.append((short, str(exc)))
                print(f"  失败  {short}: {exc}")
                continue
            report["refs"].append({"type": rtype, "url": url, **stats})
            if rtype == "DOMAIN-SET":
                print(f"  ok    {short}  {stats['domains']} 个域名")
            else:
                note = ""
                if stats["ip"] and stats["ip_no_resolve"]:
                    note = f"  ⚠ {stats['ip_no_resolve']}/{stats['ip']} 条 IP 规则缺 no-resolve"
                    report["warnings"].append(f"{short}: {stats['ip_no_resolve']} 条 IP 规则缺 no-resolve")
                print(f"  ok    {short}  {stats['rules']} 条规则{note}")

        if failures:
            print(f"\n远程校验失败 {len(failures)} 项：", file=sys.stderr)
            for short, err in failures:
                print(f"  - {short}: {err}", file=sys.stderr)
            print("\n上游可能改了目录结构或断供。修好前不发布，release 分支保持上一版可用。",
                  file=sys.stderr)
            return 1

    for w in report["warnings"]:
        print(f"  警告  {w}")

    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"已生成 {os.path.relpath(args.report, ROOT)}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        print(f"构建失败: {exc}", file=sys.stderr)
        sys.exit(1)
