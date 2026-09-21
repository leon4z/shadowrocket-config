#!/usr/bin/env python3
"""校验 src/Shadowrocket.conf，并生成 dist/Shadowrocket.conf。

这个脚本做两件事，缺一不可：

1. 校验 —— 把配置里所有远程规则集拉下来，逐条检查。上游改名、改目录、
   改格式、断供，都会在这里失败，而不是等你某天发现分流不对。
   同时检查 [Rule] 段引用的策略名是否真的在 [Proxy Group] 里定义过
   （组名改了但规则没跟着改，是小火箭里最容易出的静默错误）。
2. 生成 —— 把 src 原样加一行构建戳输出到 dist。加戳是为了让 Shadowrocket
   能识别出配置有新版本，从而触发订阅更新。

只读远程，不写任何远程位置。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "Shadowrocket.conf")

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

# 规则策略里可以直接写的内建策略（不是分组名）。
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


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #

def read_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return fh.read().split("\n")


def effective(lines: list[str]) -> list[str]:
    """去掉空行与整行注释，返回真正生效的行。"""
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def parse_config(lines: list[str]) -> dict[str, list[str]]:
    """按 [Section] 切段。段名统一小写，中括号去掉。"""
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


def parse_group(line: str) -> tuple[str, list[str]]:
    name, _, rest = line.partition("=")
    return name.strip(), [p.strip() for p in rest.split(",")]


# --------------------------------------------------------------------------- #
# 远程抓取
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


def check_ruleset(url: str, body: str) -> dict:
    """RULE-SET：每行必须是 `类型,值[,策略]`。返回统计。"""
    stats = {"rules": 0, "ip": 0, "ip_no_resolve": 0, "types": {}}
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
        stats["types"][rtype] = stats["types"].get(rtype, 0) + 1
        if rtype in IP_TYPES:
            stats["ip"] += 1
            if "no-resolve" not in parts:
                stats["ip_no_resolve"] += 1
    if stats["rules"] == 0:
        raise Failure("规则集里一条有效规则都没有")
    return stats


def check_domainset(url: str, body: str) -> dict:
    """DOMAIN-SET：每行是一个裸域名，允许前导点（.example.com）。"""
    stats = {"domains": 0}
    dom = re.compile(r"^\.?[A-Za-z0-9_*.-]+$")
    for lineno, raw in enumerate(body.split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        token = line.split()[0]
        if not dom.match(token):
            raise Failure(f"第 {lineno} 行不像域名: {line[:80]!r}")
        stats["domains"] += 1
    if stats["domains"] == 0:
        raise Failure("域名集里一条都没有")
    return stats


# --------------------------------------------------------------------------- #
# 配置级校验
# --------------------------------------------------------------------------- #

def validate_groups(sections: dict[str, list[str]]) -> tuple[set[str], list[str]]:
    names: set[str] = set()
    warnings: list[str] = []
    for line in effective(sections.get("proxy group", [])):
        name, parts = parse_group(line)
        if not name:
            raise Failure(f"分组行没有名字: {line[:80]!r}")
        if name in names:
            raise Failure(f"分组名重复: {name}")
        names.add(name)

        gtype = parts[0].lower() if parts else ""
        if gtype not in {"select", "url-test", "fallback", "load-balance", "random"}:
            raise Failure(f"分组 {name} 的类型不认识: {gtype!r}")

        filt = next((p for p in parts if p.startswith("policy-regex-filter=")), None)
        inline = [p for p in parts[1:] if "=" not in p]
        if filt:
            pattern = filt.split("=", 1)[1]
            try:
                re.compile(pattern)
            except re.error as exc:
                raise Failure(f"分组 {name} 的 policy-regex-filter 不是合法正则: {exc}")
        elif not inline:
            raise Failure(f"分组 {name} 既没有成员也没有 policy-regex-filter")
    return names, warnings


def validate_rules(sections: dict[str, list[str]], groups: set[str]) -> list[tuple[str, str]]:
    """返回 [(类型, URL)]，并按序检查策略名。

    策略名按不区分大小写比对：上游 johnshall 的配置里 [Rule] 写 `YOUTUBE`、
    [Proxy Group] 定义 `YouTube`，这套写法在实际使用中有效，说明小火箭的策略名
    查找不区分大小写。本配置沿用了同样的写法。
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
            # 策略在最后一列，但 IP 类规则后面还可能跟 no-resolve 之类的修饰词
            tail = [p for p in parts[2:] if p and p not in {"no-resolve", "force-remote-dns"}]
            policy = tail[-1] if tail else None

        if policy and policy.upper() not in known:
            raise Failure(f"[Rule] 引用了未定义的策略 {policy!r}: {line[:80]!r}")
    return refs


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description="校验并生成 Shadowrocket 配置")
    ap.add_argument("--src", default=SRC, help="源配置文件")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "Shadowrocket.conf"),
                    help="输出文件；用 - 表示写到 stdout")
    ap.add_argument("--report", default=None, help="把校验报告写成 JSON")
    ap.add_argument("--no-network", action="store_true", help="跳过远程校验（离线自检用）")
    args = ap.parse_args()

    lines = read_lines(args.src)
    sections = parse_config(lines)
    missing = {"general", "proxy group", "rule"} - set(sections)
    if missing:
        raise SystemExit(f"配置缺少必需的段: {sorted(missing)}")

    groups, warnings = validate_groups(sections)
    refs = validate_rules(sections, groups)
    print(f"配置解析完成：[Proxy Group] {len(groups)} 个组，[Rule] 引用 {len(refs)} 个远程集合")

    report: dict = {
        "source": os.path.relpath(args.src, ROOT),
        "groups": sorted(groups),
        "refs": [],
        "warnings": warnings,
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }

    if not args.no_network:
        failures = []
        for rtype, url in refs:
            short = url.split("/rule/", 1)[-1] if "/rule/" in url else url.rsplit("/", 1)[-1]
            try:
                body = fetch(url)
                stats = check_domainset(url, body) if rtype == "DOMAIN-SET" else check_ruleset(url, body)
            except Failure as exc:
                failures.append((short, str(exc)))
                print(f"  失败  {short}: {exc}")
                continue

            entry = {"type": rtype, "url": url, **stats}
            report["refs"].append(entry)
            if rtype == "DOMAIN-SET":
                print(f"  ok    {short}  {stats['domains']} 个域名")
            else:
                note = ""
                if stats["ip"] and stats["ip_no_resolve"]:
                    note = f"  ⚠ {stats['ip_no_resolve']}/{stats['ip']} 条 IP 规则缺 no-resolve"
                    warnings.append(f"{short}: {stats['ip_no_resolve']} 条 IP 规则缺 no-resolve")
                print(f"  ok    {short}  {stats['rules']} 条规则{note}")

        if failures:
            print(f"\n远程校验失败 {len(failures)} 项：", file=sys.stderr)
            for short, err in failures:
                print(f"  - {short}: {err}", file=sys.stderr)
            print("\n上游可能改了目录结构或断供。修好前不发布，release 分支保持上一版可用。",
                  file=sys.stderr)
            return 1

    for w in warnings:
        print(f"  警告  {w}")

    # 构建戳由源文件内容哈希派生，而不是时间：源文件没改就不产生新提交，
    # release 分支的每一次变更都对应一次真实的配置改动。
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:12]
    n_remote = len(report["refs"])
    header = (f"# built from src/Shadowrocket.conf sha256:{digest}"
              f" · 远程集合 {n_remote} 个已校验\n")
    out_text = header + "\n".join(lines)
    report["source_sha256"] = digest

    if args.out == "-":
        sys.stdout.write(out_text)
    else:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out_text)
        print(f"\n已生成 {os.path.relpath(args.out, ROOT)}（{len(out_text.splitlines())} 行）"
              f"，源哈希 {digest}")

    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"已生成 {os.path.relpath(args.report, ROOT)}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        print(f"校验失败: {exc}", file=sys.stderr)
        sys.exit(1)
