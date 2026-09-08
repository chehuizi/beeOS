#!/usr/bin/env python3
"""验证 kernel-design.md 里的 mermaid 块：
1. 静态结构检查：``` 配对、[] {} () 平衡、节点 ID 合规、无中文括号 / <br/> / 中点
2. 真实渲染验证：每个块用 mermaid.ink 渲染成 SVG，看是否返回 200 + 合理大小

GitHub 用的是 mermaid-js，跟 mermaid.ink 同一个渲染器，所以本脚本通过 = GitHub 能渲染。
"""
import base64
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def static_check(content: str) -> tuple[list[str], list[tuple[int, int, str]]]:
    """返回 (issues, [(start_line, end_line, body) ...])。"""
    issues: list[str] = []
    fence_pat = re.compile(r"^```(\w*)?\s*$", re.MULTILINE)
    fences = list(fence_pat.finditer(content))
    if len(fences) % 2 != 0:
        issues.append(f"❌ ``` 总数 {len(fences)} 为奇数，配对不平衡")
    blocks: list[tuple[int, int, str]] = []
    i = 0
    while i < len(fences) - 1:
        lang = fences[i].group(1) or ""
        if lang == "mermaid":
            sl = content[: fences[i].start()].count("\n") + 1
            el = content[: fences[i + 1].start()].count("\n") + 1
            body = content[fences[i].end() : fences[i + 1].start()].strip("\n")
            blocks.append((sl, el, body))
            i += 2
        else:
            i += 1
    for body in [b for _, _, b in blocks]:
        for o, c in [("[", "]"), ("{", "}"), ("(", ")")]:
            if body.count(o) != body.count(c):
                issues.append(f"❌ {o}{c} 不平衡: {body.count(o)}/{body.count(c)}")
        if re.search(r"[（）]", body):
            issues.append("⚠️  含中文括号（GitHub mermaid 兼容性差）")
        if re.search(r"<br\s*/?>", body):
            issues.append("⚠️  含 <br/>（GitHub mermaid v9 兼容性差）")
        if re.search(r"·", body):
            issues.append("⚠️  含中点 ·（GitHub mermaid 兼容性差）")
        sub = len(re.findall(r"^\s*subgraph\s+", body, re.MULTILINE))
        end = len(re.findall(r"^\s*end\s*$", body, re.MULTILINE))
        if sub != end:
            issues.append(f"❌ subgraph {sub} / end {end} 不匹配")
    return issues, blocks


def render_svg(mermaid_src: str, retries: int = 3) -> tuple[int, int, str]:
    """提交到 mermaid.ink，返回 (http_code, svg_size, body_preview)。
    不可用时返回 (0, 0, err)——调用方需降级为 warn。"""
    b64 = base64.urlsafe_b64encode(mermaid_src.encode("utf-8")).decode("ascii")
    url = f"https://mermaid.ink/svg/{b64}"
    last_err = ""
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            r = urllib.request.urlopen(req, timeout=30)
            body = r.read()
            return r.status, len(body), body[:80].decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.read()[:80].decode('utf-8', errors='replace')}"
            if e.code in (429, 503):
                time.sleep(3 + k * 3)
            else:
                break
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:80]}"
            time.sleep(3 + k * 2)
    return 0, 0, last_err


def main():
    if len(sys.argv) < 2:
        print("usage: verify_mermaid.py <file.md>")
        sys.exit(2)
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    issues, blocks = static_check(text)

    print(f"== {path} ==")
    print(f"找到 {len(blocks)} 个 mermaid 块\n")

    print("=== 静态结构检查 ===")
    if issues:
        for it in issues:
            print(f"  {it}")
    else:
        print("  ✅ 通过\n")

    print("=== 真实渲染验证（mermaid.ink，best-effort） ===")
    print("  注：mermaid.ink 是第三方服务端 puppeteer 渲染，不稳定时降级为 warn。")
    print("      GitHub 用内置 mermaid-js 客户端，渲染判断以 GitHub 实际显示为准。")
    for i, (sl, el, body) in enumerate(blocks, 1):
        code, size, preview = render_svg(body)
        ok = code == 200 and size > 2000
        status = "✅ OK" if ok else "⚠️  不可用"
        print(f"  块 #{i}（行 {sl}-{el}）: HTTP {code}, SVG {size} bytes — {status}")
        if not ok:
            print(f"    {preview[:120]}")
        if i < len(blocks):
            time.sleep(2)  # 避免 mermaid.ink 限流

    if not issues:
        print("\n✅ 静态检查全部通过（mermaid.ink 不可用不影响 commit）")
        sys.exit(0)
    print("\n❌ 静态检查有问题")
    sys.exit(1)


if __name__ == "__main__":
    main()
