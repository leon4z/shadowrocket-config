#!/usr/bin/env python3
"""从生成的 Shadowrocket 配置渲染出 Karing 能导入的分流分组。

一份源（上游 lazy_group.conf + src/overrides.py）→ 两个客户端产物：

    fallback.conf                                   个人小火箭输入
    karing/diversion_rules_custom.json
    karing/ruleset/<分类>.json                        Karing

以小火箭配置作为中间表示：它已经表达了「哪个分类 → 哪批规则集 → 哪个策略」。
这个脚本把每条规则集转成 sing-box 源码规则集（Karing 的 rule_set 字段要的格式），
再把策略映射成 Karing 的动作。

Karing 的硬限制：动作只有 direct / block / urltest / currentSelected 四种，
不能像小火箭那样给每个服务指定不同分组。所以：
    小火箭的 DIRECT            → direct
    小火箭的 REJECT*           → block
    小火箭的 速度（默认代理组） → urltest（Karing 的「自动选择」）
    小火箭的 稳定（例外代理组） → currentSelected（Karing 的「当前选择」）
想映射成别的就改 OUTBOUND_MAP。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RETRIES = 3
TIMEOUT = 40
UA = "shadowrocket-config-build/1.0"

# 小火箭策略 → Karing 动作
OUTBOUND_MAP = {
    "DIRECT": "direct",
    "REJECT": "block",
    "REJECT-DICT": "block",
    "REJECT-ARRAY": "block",
    "REJECT-200": "block",
    "REJECT-IMG": "block",
    "REJECT-TINYGIF": "block",
    "REJECT-VIDEO": "block",
    "REJECT-DROP": "block",
    "REJECT-NO-DROP": "block",
    "速度": "urltest",
    "稳定": "currentSelected",
}
DEFAULT_OUTBOUND = "urltest"

# 每个分类的显示名（Karing 里看到的名字）。没列到的用分类名本身。
DISPLAY_NAME = {
    "AI": "💬 AI 服务",
    "YouTube": "📹 油管视频",
    "Netflix": "🎥 奈飞视频",
    "Disney": "🏰 Disney+",
    "HBO": "🎬 Max (HBO)",
    "Spotify": "🎵 Spotify",
    "Telegram": "📲 电报消息",
    "PayPal": "💳 PayPal",
    "Twitter": "📲 X (Twitter)",
    "Facebook": "📲 Facebook",
    "Amazon": "📦 Amazon",
    "Sony": "🎮 游戏平台",
    "Nintendo": "🎮 游戏平台",
    "Epic": "🎮 游戏平台",
    "SteamCN": "🎮 游戏平台",
    "Steam": "🎮 游戏平台",
    "Game": "🎮 游戏平台",
    "GitHub": "🐱 GitHub",
    "Microsoft": "Ⓜ️ 微软服务",
    "Google": "🌏 Google",
    "Apple": "🍎 苹果服务",
    "BiliBili": "📺 哔哩哔哩",
    "NetEaseMusic": "🎶 网易音乐",
    "Baidu": "🔍 百度",
    "DouBan": "🎬 豆瓣",
    "WeChat": "💬 微信",
    "Sina": "📰 新浪",
    "Zhihu": "❓ 知乎",
    "XiaoHongShu": "📕 小红书",
    "DouYin": "🎵 抖音",
    "TikTok": "🎧 TikTok",
    "Global": "🌏 国外穿墙",
    "China": "🎯 国内直连",
    "Lan": "🏠 局域网",
}

# 多个小火箭规则集合并成一个 Karing 分组：小火箭里 6 个游戏集对应 Karing 一条
MERGE_INTO = {c: "游戏平台" for c in ("Sony", "Nintendo", "Epic", "SteamCN", "Steam", "Game")}


class Failure(Exception):
    pass


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
        except Exception as exc:   # 网络层什么都可能抛：URLError、IncompleteRead、超时…
            last = exc
            if attempt < RETRIES:
                time.sleep(2 * attempt)
    raise Failure(f"{type(last).__name__}: {last}")


# --------------------------------------------------------------------------- #
# 小火箭通配 → sing-box 正则
# --------------------------------------------------------------------------- #

def wildcard_to_regex(pattern: str) -> str:
    """小火箭的 DOMAIN-WILDCARD（* 和 ?）转成 sing-box 的 domain_regex。

    小火箭的 `*` 匹配任意字符（含点），`?` 匹配单个字符；整串锚定。
    """
    out = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return "^" + "".join(out) + "$"


# --------------------------------------------------------------------------- #
# 规则集转换
# --------------------------------------------------------------------------- #

def convert_list(body: str, into: dict, bare_domains: bool = False) -> dict:
    """把小火箭/圈X 方言的列表转进 sing-box 源码规则集的桶里。

    bare_domains=True 用于 _Domain.list（裸域名，带前导点，等价于 DOMAIN-SUFFIX）。
    返回丢弃类型的统计。
    """
    dropped: dict[str, int] = {}
    for lineno, raw in enumerate(body.split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if bare_domains:
            dom = line.split()[0].lstrip(".")
            if dom:
                into.setdefault("domain_suffix", set()).add(dom)
            continue

        parts = [p.strip() for p in line.split(",")]
        rtype = parts[0].upper()
        if len(parts) < 2 or not parts[1]:
            raise Failure(f"第 {lineno} 行缺少匹配值: {line[:80]!r}")
        value = parts[1]

        if rtype in ("DOMAIN-SUFFIX", "HOST-SUFFIX"):
            into.setdefault("domain_suffix", set()).add(value)
        elif rtype in ("DOMAIN", "HOST"):
            into.setdefault("domain", set()).add(value)
        elif rtype in ("DOMAIN-KEYWORD", "HOST-KEYWORD"):
            into.setdefault("domain_keyword", set()).add(value)
        elif rtype in ("DOMAIN-WILDCARD", "HOST-WILDCARD"):
            into.setdefault("domain_regex", set()).add(wildcard_to_regex(value))
        elif rtype in ("IP-CIDR", "IP6-CIDR", "IP-CIDR6"):
            into.setdefault("ip_cidr", set()).add(value)
        elif rtype == "PROCESS-NAME":
            into.setdefault("process_name", set()).add(value)
        else:
            # USER-AGENT / URL-REGEX / IP-ASN 在 sing-box 的规则集里没有对应字段
            dropped[rtype] = dropped.get(rtype, 0) + 1
    return dropped


def to_ruleset(buckets: dict) -> dict:
    """桶 → sing-box 源码规则集。字段顺序固定，保证输出可重复。"""
    rules = []
    order = ("domain", "domain_suffix", "domain_keyword", "domain_regex",
             "ip_cidr", "process_name")
    for key in order:
        vals = sorted(buckets.get(key) or [])
        if vals:
            rules.append({key: vals})
    return {"version": 1, "rules": rules}


# --------------------------------------------------------------------------- #
# 解析小火箭配置
# --------------------------------------------------------------------------- #

def parse_rule_lines(conf_text: str) -> list[tuple[str, str, str]]:
    """按出现顺序返回 [(分类名, 源 URL 或通配, 策略)]。

    通配规则挂到**紧邻的上一个规则集分类**上——小火箭配置里 16 条 DOMAIN-WILDCARD
    就紧跟在 Apple / China 那两个规则集之后，它们属于同一分类。
    """
    section = None
    out: list[tuple[str, str, str]] = []
    last_category = None
    for raw in conf_text.split("\n"):
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip().lower()
            continue
        if section != "rule" or not s or s.startswith("#"):
            continue
        parts = [p.strip() for p in s.split(",")]
        rtype = parts[0].upper()
        if rtype in ("RULE-SET", "DOMAIN-SET") and len(parts) >= 3:
            category = MERGE_INTO.get(category_of(parts[1]), category_of(parts[1]))
            last_category = category
            out.append((category, parts[1], parts[2]))
        elif rtype == "DOMAIN-WILDCARD" and len(parts) >= 3 and last_category:
            out.append((last_category, parts[1], parts[2]))
    return out


def category_of(url: str) -> str:
    m = re.search(r"/rule/(?:Shadowrocket|Clash|QuantumultX)/([^/]+)/", url)
    if m:
        return m.group(1)
    base = url.rsplit("/", 1)[-1]
    return base.split(".")[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="渲染 Karing 的分流分组")
    ap.add_argument("--conf", default=os.path.join(ROOT, "dist", "fallback.conf"),
                    help="作为中间表示的小火箭配置")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "dist", "karing"))
    ap.add_argument("--base-url", default="https://cdn.jsdelivr.net/gh/leon4z/shadowrocket-config@release",
                    help="发布后规则集的访问前缀，写进 JSON")
    ap.add_argument("--no-network", action="store_true", help="只解析配置，不下载规则集")
    args = ap.parse_args()

    conf = open(args.conf, encoding="utf-8").read()
    entries = parse_rule_lines(conf)
    if not entries:
        raise SystemExit(f"{args.conf} 里没解析到 RULE-SET/DOMAIN-SET")

    # 按分类聚合：分类 → {策略, 源 URL 列表, 通配列表}
    cats: dict[str, dict] = {}
    for category, value, policy in entries:
        c = cats.setdefault(category, {"policy": policy, "urls": [], "wildcards": []})
        if value.startswith("http"):
            c["urls"].append(value)
        else:
            c["wildcards"].append(value)
        if policy != c["policy"]:
            print(f"  警告  {category} 的策略不一致（{c['policy']} / {policy}），按 {c['policy']} 处理")

    print(f"从 {os.path.relpath(args.conf, ROOT)} 解析出 {len(cats)} 个分类：")
    for name in cats:
        c = cats[name]
        print(f"  {name:<16} → {OUTBOUND_MAP.get(c['policy'], DEFAULT_OUTBOUND):<15}"
              f"{len(c['urls'])} 个规则集"
              + (f" + {len(c['wildcards'])} 条通配" if c["wildcards"] else ""))

    os.makedirs(os.path.join(args.out_dir, "ruleset"), exist_ok=True)
    rules = []
    total_dropped: dict[str, int] = {}

    for name, c in cats.items():
        buckets: dict[str, set] = {}
        dropped: dict[str, int] = {}
        if not args.no_network:
            for url in c["urls"]:
                body = fetch(url)
                d = convert_list(body, buckets, bare_domains=url.endswith("_Domain.list"))
                for k, v in d.items():
                    dropped[k] = dropped.get(k, 0) + v
        for pattern in c["wildcards"]:
            buckets.setdefault("domain_regex", set()).add(wildcard_to_regex(pattern))

        ruleset = to_ruleset(buckets)
        rel = f"ruleset/{name}.json"
        with open(os.path.join(args.out_dir, rel), "w", encoding="utf-8") as fh:
            json.dump(ruleset, fh, ensure_ascii=False, indent=1)
            fh.write("\n")

        n = sum(len(v) for r in ruleset["rules"] for v in r.values())
        rules.append({
            "name": DISPLAY_NAME.get(name, name),
            "outbound": OUTBOUND_MAP.get(c["policy"], DEFAULT_OUTBOUND),
            "switch": True,
            "or": True,
            "rule_set": [f"{args.base_url}/karing/{rel}"],
        })
        for k, v in dropped.items():
            total_dropped[k] = total_dropped.get(k, 0) + v
        print(f"  生成 {rel}  {n} 条")

    # 兜底：小火箭的 GEOIP,CN,DIRECT 和 FINAL 用 Karing 内置集表达
    rules.insert(0, {
        "name": "🎯 国内直连（内置）", "outbound": "direct", "switch": True, "or": True,
        "rule_set_build_in": ["acl:ChinaIp", "acl:ChinaDomain", "acl:ChinaCompanyIp", "acl:UnBan"],
    })
    rules.append({
        "name": "🌏 国外兜底", "outbound": OUTBOUND_MAP.get("速度", DEFAULT_OUTBOUND),
        "switch": True, "or": True,
        "rule_set_build_in": ["geosite:geolocation-!cn", "acl:ProxyGFWlist", "acl:ProxyMedia"],
    })
    # 分组定向测试：Karing 的 outbound 是否接受任意分组名
    rules.append({
        "name": "🧪 测试-分组定向", "outbound": "稳定", "switch": False, "or": True,
        "rule_set_build_in": ["acl:Claude"],
    })

    out_json = os.path.join(args.out_dir, "diversion_rules_custom.json")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump({"rules": rules}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"\n已生成 {os.path.relpath(out_json, ROOT)}，共 {len(rules)} 条")
    if total_dropped:
        print("以下类型在 sing-box 规则集里没有对应字段，已丢弃：")
        for k, v in sorted(total_dropped.items(), key=lambda x: -x[1]):
            print(f"  {k}: {v} 条")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        print(f"渲染失败: {exc}", file=sys.stderr)
        sys.exit(1)
