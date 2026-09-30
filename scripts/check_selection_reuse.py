#!/usr/bin/env python3
"""Allow an off-schedule build only with the exact selection already on release."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess
import sys

SELECTION = Path(__file__).resolve().parents[1] / "src" / "selection.json"


def verify(selection: bytes, published_digest: str) -> None:
    digest = published_digest.strip()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("release 缺少有效的清单哈希，必须生成新清单正常发布")
    if hashlib.sha256(selection).hexdigest() != digest:
        raise ValueError("清单与 release 已发布版本不同，必须生成新清单正常发布")


def write_digest(selection: bytes, output: Path) -> None:
    output.write_text(hashlib.sha256(selection).hexdigest() + "\n", encoding="ascii")


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--write":
        write_digest(SELECTION.read_bytes(), Path(sys.argv[2]))
        return
    if len(sys.argv) != 1:
        raise SystemExit("用法：check_selection_reuse.py [--write <release/selection.sha256>]")
    try:
        published_digest = subprocess.check_output(
            ["git", "show", "refs/remotes/origin/release:selection.sha256"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        verify(SELECTION.read_bytes(), published_digest)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise SystemExit(str(exc) or "无法核对 release 清单哈希") from exc
    print("手动发布复用已发布清单：字节哈希一致")


if __name__ == "__main__":
    main()
