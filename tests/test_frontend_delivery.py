"""tests/test_frontend_delivery.py - 「前端也要走盒子链」的守卫

**为什么单独一个文件**：前端支持是**一次机制改造**（三只盒子各动一处），
不是一次需求交付。这三处如果只有两处生效，链条会**半通**——
后端做完了、页面没有，而报告上看不出来。

这三处分别是：

| 位置 | 承担什么 | 这份文件守什么 |
|---|---|---|
| 设计盒 zone statement | UI 契约（页面/路由/api 文件/菜单） | 契约里真有这几项 |
| 开发盒 code_map + codegen | 前端落点 + 采集 + 前端构建 | 落点能采到、只 import 不算数、构建失败能定位 |
| 测试盒 probes | 前端产物探针 | 三态分明、缺页面判红 |

**每条守卫都必须能独立触发它要守的那个失败**：
别的闸先拦下来 = 这条守卫不承重。
所以每条都有「注入」——真的造一个坏样本出来。
"""
from __future__ import annotations

import pathlib
import shutil

import pytest

from boxes.wms_development import codegen
from boxes.wms_development.build import (
    BuildError,
    extract_frontend_errors,
    npm_cmd,
    run_frontend_build,
)
from boxes.wms_development.code_map import (
    EVIDENCE_LITERAL,
    EVIDENCE_TS_USAGE,
    implementation_evidence,
    locations_for,
    missing_evidence,
)
from boxes.wms_testing.probes import (
    OUTCOME_FAIL,
    OUTCOME_INDETERMINATE,
    OUTCOME_PASS,
    ProbeContext,
    _check_zone_frontend,
)


# ============================================================
# 1) 设计盒：UI 契约在 zone 的 statement 里
# ============================================================

def test_zone_statement_carries_the_ui_contract():
    """zone 的 statement 必须写清前端那几项

    **为什么这是根**：契约是**三方约定的来源**——
    设计盒出预期 → 开发盒按它实现 → 测试盒按它探针。
    契约里没写「菜单标题叫库区管理」，测试盒那条 `/zones` 之外的判据
    就**没有依据**（判据得从契约来，不是从实现来）。
    """
    from boxes.wms_design.domain import WMS_ENTITIES

    statement = WMS_ENTITIES["zone"]["statement"]
    for must in ("web/src/views/zone", "web/src/api/zone",
                 "web/src/router.ts", "库区管理", "/api/v1/zones"):
        assert must in statement, f"zone 契约里少了「{must}」"


# ============================================================
# 2) 开发盒：前端落点 + 采集
# ============================================================

def test_zone_locations_include_the_frontend():
    """zone 的落点里必须有前端那几处

    没有落点 → 模型改了前端文件会被判成「改了方案没覆盖的文件」
    → 它要么不写前端，要么改了被判越界（实测：加了 zoneId 的库位实体被判越界）。
    """
    locations = locations_for(["zone"])
    for must in ("web/src/views/zone", "web/src/api/zone",
                 "web/src/router.ts", "web/src/layouts"):
        assert must in locations, f"zone 落点里少了前端位置「{must}」"


def test_collect_context_picks_up_frontend_files(tmp_path):
    """采集要能收到 web/src 下的文件

    **注入**：造一个 worktree 形状的目录，
    里面只有前端文件——以前这条链上开发盒是**瞎写前端的**。
    """
    ws = tmp_path / "ws"
    web = ws / "web" / "src" / "views" / "zone"
    web.mkdir(parents=True)
    (web / "index.vue").write_text(
        "<template><div>库区管理</div></template>", encoding="utf-8"
    )
    api = ws / "web" / "src" / "api"
    api.mkdir(parents=True)
    (api / "zone.ts").write_text("export const zoneApi = {}", encoding="utf-8")

    files = codegen.collect_context(ws, ["web/src/views/zone", "web/src/api/zone"])

    assert any("views/zone/index.vue" in rel for rel in files), \
        f"采集没收到前端页面：{sorted(files)}"
    assert any("api/zone.ts" in rel for rel in files), \
        f"采集没收到前端接口封装：{sorted(files)}"


def test_collect_all_sources_sees_frontend(tmp_path):
    """证据核对要能看到前端文件（不只是喂给模型的那份上下文）"""
    ws = tmp_path / "ws"
    page = ws / "web" / "src" / "views" / "zone"
    page.mkdir(parents=True)
    (page / "index.vue").write_text(
        "const zoneApi = 1\nzoneApi.page()\n", encoding="utf-8"
    )

    sources = codegen.collect_all_sources(ws)

    assert any("views/zone/index.vue" in rel for rel in sources), \
        "证据核对看不见前端文件 → 前端证据永远判「没实现」"


# ============================================================
# 3) 前端证据：只 import 不算数 / 路径字符串不算数
# ============================================================

def test_zone_frontend_evidence_rejects_import_only():
    """**只 import 库区接口，不调用** = 假实现，必须判「没实现」

    **注入**：真的造一个只 import 的页面。
    这是前端版的「import + 注入字段、一行业务逻辑都没写」——
    同一族病：只提到、没用上。
    """
    files = {
        "web/src/api/zone.ts": "export const zoneApi = { page() {} }",
        "web/src/views/zone/index.vue": (
            "<script setup lang=\"ts\">\n"
            "import { zoneApi } from '@/api/zone'\n"
            "</script>\n"
            "<template><div>库区管理</div></template>\n"
        ),
    }
    lacking = missing_evidence("zone", files, "\n".join(files.values()))

    hints = {hint for hint, _tok in lacking}
    assert "web/src/views/zone" in hints, \
        f"只 import 的页面被判成「用上了」：lacking={lacking}"


def test_zone_frontend_evidence_accepts_real_call():
    """真的调用了 → 证据算数（别把这条守卫写成「永远判红」）"""
    files = {
        "web/src/api/zone.ts": "export const zoneApi = { page() {} }",
        "web/src/views/zone/index.vue": (
            "<script setup lang=\"ts\">\n"
            "import { zoneApi } from '@/api/zone'\n"
            "const list = zoneApi.page\n"
            "</script>\n"
        ),
    }
    lacking = missing_evidence("zone", files, "\n".join(files.values()))

    assert "web/src/views/zone" not in {h for h, _t in lacking}, \
        f"真的调用了还判没实现：lacking={lacking}"


def test_zone_route_evidence_rejects_path_only_mention():
    """**路由证据只认真的注册**，不认文件路径里带的 zone

    `import('@/views/zone/index.vue')` 里有 zone，
    但那是**文件路径**，不是路由——用它冒充「注册了路由」就是假绿。
    """
    only_import = {
        "web/src/router.ts": (
            "const routes = [{ path: '/x', component: () => "
            "import('@/views/zone/index.vue') }]\n"
        ),
    }
    lacking = missing_evidence("zone", only_import, "\n".join(only_import.values()))
    assert "web/src/router.ts" in {h for h, _t in lacking}, \
        "文件路径里的 zone 被当成了路由注册"

    real_route = {
        "web/src/router.ts": (
            "const routes = [{ path: 'zone', component: () => "
            "import('@/views/zone/index.vue') }]\n"
        ),
    }
    lacking = missing_evidence("zone", real_route, "\n".join(real_route.values()))
    assert "web/src/router.ts" not in {h for h, _t in lacking}, \
        f"真的注册了路由还判没实现：lacking={lacking}"


def test_frontend_evidence_modes_are_declared():
    """前端证据必须带模式（不带模式 = any = 只 import 就骗过）"""
    zone_evidence = implementation_evidence("zone")
    modes = {
        hint: (rest[0] if rest else "any")
        for hint, _tok, *rest in zone_evidence
    }
    assert modes.get("web/src/views/zone") == EVIDENCE_TS_USAGE
    assert modes.get("web/src/router.ts") == EVIDENCE_LITERAL


# ============================================================
# 4) 前端构建：环境不具备 ≠ 构建失败
# ============================================================

def test_npm_cmd_raises_build_error_when_absent(monkeypatch):
    """找不到 npm → BuildError（**不是**「构建失败」）

    两者混成一个结论，看板上会显示成「页面编译不过」——
    而真相是这台机器根本没装 npm。
    """
    monkeypatch.delenv("BEEOS_NPM", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(BuildError):
        npm_cmd()


def test_run_frontend_build_reports_failure_with_errors(tmp_path, monkeypatch):
    """前端构建挂了 → ok=False，且错误里有 vue-tsc 的位置

    **注入**：造一个假的 npm，输出带类型错误的文本。
    """
    ws = tmp_path / "ws"
    (ws / "web").mkdir(parents=True)
    (ws / "web" / "package.json").write_text("{}", encoding="utf-8")

    fake = tmp_path / "fake-npm"
    fake.write_text(
        "#!/bin/sh\n"
        "echo \"src/views/zone/index.vue(12,5): error TS2345: "
        "Argument of type 'string' is not assignable.\"\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("BEEOS_NPM", str(fake))

    result = run_frontend_build(ws)

    assert result["ok"] is False
    assert "TS2345" in result["errors"]
    assert "index.vue" in result["errors"]


def test_extract_frontend_errors_keeps_only_error_lines():
    """只摘错误行（整份 vite 输出会淹掉模型）"""
    out = "\n".join([
        "vite v6.0.7 building for production...",
        "✓ 12 modules transformed",
        "src/views/zone/index.vue(3,10): error TS2304: Cannot find name 'zoneApi'.",
        "some other noise",
    ])

    errors = extract_frontend_errors(out)

    assert "TS2304" in errors
    assert "modules transformed" not in errors


# ============================================================
# 5) 测试盒：前端探针的三态
# ============================================================

def _ctx(**kw) -> ProbeContext:
    base = dict(
        client=None, mode="verification", frontend_expected=True,
    )
    base.update(kw)
    return ProbeContext(**base)


def test_frontend_probe_is_indeterminate_when_no_frontend_expected():
    """这次压根没前端 → indeterminate，**不是 pass**

    「没验到」的三种形态不许混成「通过」。
    """
    result = _check_zone_frontend(_ctx(frontend_expected=False), {})

    assert result.outcome == OUTCOME_INDETERMINATE
    assert "不等于" in result.summary or "没验" in result.summary


def test_frontend_probe_is_indeterminate_when_dist_missing(tmp_path):
    """产物路径没有 / 目录是空的 → indeterminate（现场没条件）"""
    missing = _check_zone_frontend(_ctx(frontend_dist_path=""), {})
    assert missing.outcome == OUTCOME_INDETERMINATE

    empty = tmp_path / "dist"
    empty.mkdir()
    no_files = _check_zone_frontend(_ctx(frontend_dist_path=str(empty)), {})
    assert no_files.outcome == OUTCOME_INDETERMINATE


def test_frontend_probe_fails_when_page_missing(tmp_path):
    """产物里没有库区页面 → fail，并说清缺什么

    **注入**：造一个只有后端 chunk 的产物目录。
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "index-aaa.js").write_text(
        "console.log('nothing to do with zones')", encoding="utf-8"
    )

    result = _check_zone_frontend(_ctx(frontend_dist_path=str(dist)), {})

    assert result.outcome == OUTCOME_FAIL
    assert "/zones" in result.summary
    assert result.details["has_zone_api_call"] is False


def test_frontend_probe_fails_when_route_missing(tmp_path):
    """页面调了接口但没注册进菜单/路由 → fail

    **注入**：产物里有 `/zones`，但没有菜单标题也没有 'zone' 路由。
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "zone-aaa.js").write_text(
        "const r = request.get('/zones')", encoding="utf-8"
    )

    result = _check_zone_frontend(_ctx(frontend_dist_path=str(dist)), {})

    assert result.outcome == OUTCOME_FAIL
    assert "库区管理" in result.summary
    assert result.details["has_zone_api_call"] is True
    assert result.details["has_zone_menu_title"] is False


def test_frontend_probe_passes_on_complete_artifact(tmp_path):
    """齐全 → pass（别把这条守卫写成「永远判红」）"""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "zone-aaa.js").write_text(
        "const r = request.get('/zones')\nconst t = '库区管理'", encoding="utf-8"
    )
    (dist / "assets" / "router-aaa.js").write_text(
        "path: 'zone'", encoding="utf-8"
    )

    result = _check_zone_frontend(_ctx(frontend_dist_path=str(dist)), {})

    assert result.outcome == OUTCOME_PASS
    assert result.details["has_zone_api_call"] is True
    assert result.details["has_zone_menu_title"] is True


def test_frontend_probe_registered_on_zone_concept():
    """前端那条探针必须挂在 zone 概念上（否则这条预期没人验）"""
    from boxes.wms_testing.probes import SCENARIOS

    zone_probes = [
        p for p in SCENARIOS["zone_management"].probes if p.concept == "zone"
    ]

    assert len(zone_probes) >= 2, \
        "zone 概念下没有前端探针——后端做完了页面上没有，没人发现"


# ============================================================
# 6) 接线不漂：测试盒声明的字段 = 开发盒产出的字段
# ============================================================

def test_testing_box_declares_frontend_fields_the_dev_box_produces():
    """**接线不漂**：测试盒声明的制品形状，开发盒得产得出

    开发盒多产一个字段没关系（runtime 按测试盒的声明校验），
    但**测试盒声明了、开发盒没产出** → 交接失败（响的，不是哑的）。
    """
    from boxes.wms_development.schemas import BUILD_ARTIFACT_SCHEMA
    from boxes.wms_testing.schemas import BUILD_ARTIFACT_INPUT_SCHEMA

    produced = {f.name for f in BUILD_ARTIFACT_SCHEMA.fields}
    declared = {f.name for f in BUILD_ARTIFACT_INPUT_SCHEMA.fields}

    missing = declared - produced
    assert not missing, \
        f"测试盒声明了这些字段，开发盒没产出：{sorted(missing)}"


def test_frontend_expected_is_required_on_both_sides():
    """`frontend_expected` 必须两边都必填

    不必填的话，「没有前端」和「上游忘了说」分不开——
    前端探针就可能悄悄跳过（那又是一次假绿）。
    """
    from boxes.wms_development.schemas import BUILD_ARTIFACT_SCHEMA
    from boxes.wms_testing.schemas import BUILD_ARTIFACT_INPUT_SCHEMA

    for schema in (BUILD_ARTIFACT_SCHEMA, BUILD_ARTIFACT_INPUT_SCHEMA):
        field = next(f for f in schema.fields if f.name == "frontend_expected")
        assert field.required, f"{schema.id} 的 frontend_expected 不该是可选的"
