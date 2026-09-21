#!/usr/bin/env python3
"""把 build.py 的构建报告渲染成 Markdown，供 GitHub Actions 的 Step Summary 使用。

用法：python3 scripts/summary.py dist/report.json >> "$GITHUB_STEP_SUMMARY"
报告不存在时输出一行说明而不是报错 —— 构建可能在抓取前就失败了，
那种情况下运行摘要里应该留下痕迹，而不是让这一步再失败一次。
"""

from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else "dist/report.json"
    try:
        with open(path, encoding="utf-8") as fh:
            r = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print("## 构建结果\n")
        print(f"没有可用的报告（{type(exc).__name__}: {exc}）。")
        print("构建在抓取上游或规则集之前就失败了，请看上面的步骤日志。")
        return 0

    rule_sets = [x for x in r["refs"] if x["type"] == "RULE-SET"]
    domain_sets = [x for x in r["refs"] if x["type"] == "DOMAIN-SET"]

    print("## 构建结果\n")
    print(f"上游版本标记 `{r['upstream_rev']}` · 内容哈希 `{r['upstream_sha256']}`")
    print(f"（{r['upstream_url']}）\n")

    print("### 产物\n")
    print("| 文件 | 组 | 远程集合 | 行数 |")
    print("| --- | --- | --- | --- |")
    for v in r["variants"]:
        print(f"| `{v['file']}` | {v['groups']} | {v['refs']} | {v['lines']} |")

    print("\n### 规则集校验\n")
    print(f"- RULE-SET {len(rule_sets)} 个，规则 {sum(x['rules'] for x in rule_sets)} 条，"
          f"其中 IP 类 {sum(x['ip'] for x in rule_sets)} 条，"
          f"缺 `no-resolve` {sum(x['ip_no_resolve'] for x in rule_sets)} 条")
    print(f"- DOMAIN-SET {len(domain_sets)} 个，域名 {sum(x['domains'] for x in domain_sets)} 条")

    if r.get("warnings"):
        print("\n### 警告\n")
        for w in r["warnings"]:
            print(f"- {w}")
    else:
        print("\n无警告：所有 IP 规则都带 `no-resolve`。")

    print("\n<details><summary>各规则集条目数</summary>\n")
    print("| 引用 | 条目 |")
    print("| --- | --- |")
    for x in r["refs"]:
        name = x["url"].split("/rule/")[-1]
        if x["type"] == "DOMAIN-SET":
            print(f"| `{name}` | {x['domains']} 域名 |")
        else:
            extra = f"，{x['ip']} IP" if x["ip"] else ""
            print(f"| `{name}` | {x['rules']} 规则{extra} |")
    print("\n</details>")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
