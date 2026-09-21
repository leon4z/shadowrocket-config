#!/usr/bin/env python3
"""拉上游 lazy_group.conf，套用 src/overrides.py 的规格，产出两份配置。

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


def tune_group(line: str, spec, variant: dict) -> str:
    """调参：删掉按名指定默认项的参数、tolerance 统一、必要时替换 PROXY。"""
    name, params = split_params(line)

    params = [p for p in params
              if not any(p.startswith(f"{d}=") for d in spec.DROP_GROUP_PARAMS)]

    params = [f"tolerance={spec.TOLERANCE}" if p.startswith("tolerance=") else p
              for p in params]

    if variant["substitute_proxy"]:
        target = spec.PROXY_TARGET_PER_GROUP.get(name, spec.PROXY_TARGET_DEFAULT)
        params = [target if p == "PROXY" else p for p in params]

    return join_params(name, params)


def expand_rule(line: str, spec, variant: dict) -> list[str]:
    """[Rule] 行：方言替换 + 策略替换。返回若干行（方言替换会一行变多行）。"""
    params = [p.strip() for p in line.split(",")]
    rtype = params[0].upper()

    dialect = {d["qx"]: d for d in spec.RULESET_DIALECT}
    if rtype == "RULE-SET" and len(params) >= 3 and params[1] in dialect:
        d = dialect[params[1]]
        policy = substitute_policy(params[2], None, spec, variant)
        out = [f"RULE-SET,{d['sr']},{policy}"]
        if d["sr_domain_set"]:
            out.append(f"DOMAIN-SET,{d['sr_domain_set']},{policy}")
        out.extend(f"DOMAIN-WILDCARD,{w},{policy}" for w in d["wildcards"])
        return out

    if rtype in {"RULE-SET", "DOMAIN-SET"} and len(params) >= 3:
        params[2] = substitute_policy(params[2], None, spec, variant)
    elif rtype == "FINAL" and len(params) >= 2 and params[1]:
        params[1] = substitute_policy(params[1], None, spec, variant)
    elif len(params) >= 3:
        params[-1] = substitute_policy(params[-1], None, spec, variant)

    return [",".join(params)]


def substitute_policy(policy: str, group_name, spec, variant: dict) -> str:
    if not variant["substitute_proxy"] or policy != "PROXY":
        return policy
    if group_name:
        return spec.PROXY_TARGET_PER_GROUP.get(group_name, spec.PROXY_TARGET_DEFAULT)
    return spec.PROXY_TARGET_DEFAULT


def transform(upstream: str, spec, variant: dict) -> list[str]:
    out: list[str] = []
    section = None
    for raw in upstream.split("\n"):
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip().lower()
            out.append(raw)
            if section == "proxy group" and variant["substitute_proxy"]:
                out.append("")
                out.append("# 本地定制：把上游指向内置 PROXY 的地方改为指向以下两个组。")
                out.append("# 底层节点按名称正则从订阅里筛，订阅换代不需要改配置。")
                out.extend(spec.EXTRA_GROUPS)
            continue
        if s and not s.startswith("#"):
            if section == "proxy group":
                out.append(tune_group(raw, spec, variant))
                continue
            if section == "rule":
                out.extend(expand_rule(raw, spec, variant))
                continue
        out.append(raw)
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
        if filt:
            try:
                re.compile(filt.split("=", 1)[1])
            except re.error as exc:
                raise Failure(f"分组 {name} 的 policy-regex-filter 不是合法正则: {exc}")
        elif not inline:
            raise Failure(f"分组 {name} 既没有成员也没有 policy-regex-filter")
    return names


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
    ap.add_argument("--no-network", action="store_true",
                    help="用 dist/upstream-lazy_group.conf 缓存代替联网抓取（离线自检用）")
    args = ap.parse_args()

    spec = load_spec()
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
    }

    built: dict[str, tuple[str, list[tuple[str, str]]]] = {}

    for variant in spec.VARIANTS:
        lines = transform(upstream, spec, variant)
        sections = parse_sections(lines)

        # 生成后的自检：不该再有圈X 引用；custom 变体不该再有裸 PROXY 策略
        text = "\n".join(lines)
        leftover = [u for u in re.findall(r"https://\S+?\.list", text) if "/QuantumultX/" in u]
        if leftover:
            raise Failure(f"{variant['id']}: 仍有圈X 引用 {leftover}")
        if variant["substitute_proxy"]:
            bad = [l for l in effective(sections["proxy group"])
                   if "PROXY" in [p.strip() for p in l.split(",")]]
            if bad:
                raise Failure(f"{variant['id']}: 仍有指向 PROXY 的分组 {bad[:2]}")

        groups = validate_groups(sections)
        refs = validate_rules(sections, groups)

        header = [
            f"# 由 leon4z/shadowrocket-config 生成 · {variant['title']}",
            f"# 上游 {spec.UPSTREAM_URL}",
            f"# 上游版本标记 {upstream_rev} · 上游内容 sha256:{upstream_hash}",
            "# 上游 + 覆盖规格，差异见仓库 README。不要直接改这个文件。",
        ]
        out_text = "\n".join(header) + "\n" + "\n".join(lines)
        path = os.path.join(args.out_dir, f"{variant['id']}.conf")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(out_text)

        built[variant["id"]] = (out_text, refs)
        report["variants"].append({
            "id": variant["id"],
            "title": variant["title"],
            "file": os.path.basename(path),
            "groups": len(groups),
            "refs": len(refs),
            "lines": len(out_text.splitlines()),
        })
        print(f"  {variant['id']}.conf  {len(groups)} 组 / {len(refs)} 个远程集合 / "
              f"{len(out_text.splitlines())} 行")

    # 远程集合校验：两个变体引用的集合完全一致，只抓一遍
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
