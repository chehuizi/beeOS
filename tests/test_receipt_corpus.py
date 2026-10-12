"""tests.test_receipt_corpus - 真票回归集

这个目录（`tests/fixtures/receipts/`）里是**真拍的小票**，不是合成的。
每一张都对应一次真实踩坑，坑在这里被钉成断言。

为什么要有它：过去连着四次**误伤真票**——

    POS 小票没有税栏        → 「小计+税=实付」恒红
    餐馆结账单没有单价栏    → 「数量×单价=金额」恒红
    折后商品有行级折扣      → 原价 × 数量 ≠ 折后金额
    烘焙单折让已含在合计里  → 减掉折让就对不上实付

四次都是**判据假定了某类票的形状**，产出本身没错。四次都是真票实跑才发现，
四次都是烧掉一次模型调用才定位。修完之后没有任何东西防止第五次。

这里补上的就是那个东西。

**只钉机械层，不钉模型输出。** 每份真票记两样：

- `parsed` —— 票面**真实印着**的解析（人工逐字核对），这是金标准
- `expect` —— 这份**正确**的解析喂进机械层，应该得出什么判定

这是盒子自己的性质，与模型版本无关。拿模型输出当断言等于把测试写成
随机数发生器——模型每版每次都不一样，断言只会假红，然后被注释掉。

代价是这个文件只覆盖机械层——它不需要凭证、不花钱、结果确定。
要真跑模型看抽取质量用 `BEEOS_E2E=1 pytest tests/test_receipt_corpus.py`，
那条断言的是「机械层收不收」，不是「字抄对没有」。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from boxes.receipt.runner import _simulate_package_receipt as package
from boxes.receipt.runner import _simulate_verify_receipt as verify

CORPUS = Path(__file__).parent / "fixtures" / "receipts"
TRUTH = json.loads((CORPUS / "truth.json").read_text(encoding="utf-8"))
RECEIPTS = TRUTH["receipts"]
RULES = (
    "field_completeness", "line_arithmetic", "sum_arithmetic",
    "total_arithmetic", "date_wellformed", "totals_match",
)


def _ids() -> list[str]:
    return [r["id"] for r in RECEIPTS]


def _same(got: object, want: object) -> bool:
    """数字按值比，其余按字符串比

    `1` 和 `1.0` 是同一个数，模型那边可能带小数点这边不带；
    按字符串比会把它判成错，然后这条断言就成了噪声。
    """
    if got is None or want is None:
        return got is want
    try:
        return abs(float(got) - float(want)) <= 0.001
    except (TypeError, ValueError):
        return str(got).strip() == str(want).strip()


class TestCorpusIsWorthSomething:
    """先确认这份语料本身是有效的，否则下面全是在测空气"""

    def test_it_is_not_empty(self):
        assert len(RECEIPTS) >= 3, "真票少于 3 张，这份语料说明不了问题"

    def test_every_shape_is_distinct(self):
        """四张票得覆盖四种不同形状，否则重复采样、覆盖没长进"""
        shapes = [r["shape"] for r in RECEIPTS]
        assert len(set(shapes)) == len(shapes), f"票型重复了：{shapes}"

    def test_every_receipt_has_a_photo_on_disk(self):
        for r in RECEIPTS:
            assert (CORPUS / r["photo"]).is_file(), (
                f"{r['id']} 的照片不在：{r['photo']}（truth.json 跟目录对不上）"
            )

    def test_every_expect_covers_every_rule(self):
        for r in RECEIPTS:
            missing = set(RULES) - set(r["expect"])
            assert not missing, f"{r['id']} 的 expect 漏了判据：{sorted(missing)}"

    def test_known_gaps_are_written_down_not_swept_under(self):
        """预期红的票必须**指名**它卡在哪个已知坑上

        「红色必须有名字」。没有名字的话，下一个看到红的人只会当成
        bug 直接改绿——而那正是给一个还没解决的问题盖绿章。
        这里同时验两件事：指名了，而且指的那个坑真的在 known_gaps 里。
        """
        gaps = {g["gap"] for g in TRUTH["known_gaps"]}
        assert gaps, "known_gaps 空了——要么全修了，要么忘了记"
        for r in RECEIPTS:
            if r["expect"]["acceptance"] == "accepted":
                assert "blocked_by" not in r, (
                    f"{r['id']} 预期能过，却挂着 blocked_by={r.get('blocked_by')!r}"
                )
                continue
            assert r.get("blocked_by") in gaps, (
                f"{r['id']} 预期红，但 blocked_by={r.get('blocked_by')!r} "
                f"不在 known_gaps 里（{sorted(gaps)}）"
            )

    def test_a_corpus_that_only_ever_expects_green_is_suspicious(self):
        """如果哪天全绿了，多半是有人把红色改成了绿色，而不是把问题修了

        这条不是抬杠：语料的作用就是把「现状」和「期望」分开钉住。
        改 expect 是一次「改测试」，而改判据是「修问题」——
        后者要连着 truth.json 的照片一起看，前者只要动两行字。
        """
        red = [r["id"] for r in RECEIPTS if r["expect"]["acceptance"] != "accepted"]
        assert TRUTH["known_gaps"], "有红票却没记 known_gaps，红得没有名字" if red else None
        if not red and not TRUTH["known_gaps"]:
            pytest.fail("语料里一张红票都没有，也没有任何已知坑——真的全修好了？")


class TestCorrectParseGetsTheRightVerdict:
    """**正确**的解析必须得出正确的判定

    这一层是过去四次误伤真票的地方。票型一变就重犯，
    而这里每次改判据都会立刻验一遍全部真票。
    """

    @pytest.mark.parametrize("rec", RECEIPTS, ids=_ids())
    def test_every_rule_matches_the_expected_verdict(self, rec):
        got = verify({"parsed": rec["parsed"]})
        wrong = {k: (rec["expect"][k], got[k]) for k in RULES if got[k] != rec["expect"][k]}
        assert not wrong, (
            f"{rec['id']}（{rec['shape']}）判错了：\n"
            + "\n".join(f"  {k}: 期望 {e!r} 实际 {a!r}" for k, (e, a) in wrong.items())
        )

    @pytest.mark.parametrize("rec", RECEIPTS, ids=_ids())
    def test_n_a_only_where_the_ticket_lacks_the_column(self, rec):
        """n/a 只能是「票上没这一栏」，不能是「抄砸了」

        这里反向查一遍：每张票 n/a 的理由必须写在 shape 里。
        不适用判据一旦泛化，票型对不上的真票就会被悄悄放行。
        """
        for k in RULES:
            if rec["expect"][k] == "n/a":
                assert "没有单价栏" in rec["shape"], (
                    f"{rec['id']} 的 {k} 判成不适用，但票型里没写「没有单价栏」"
                )

    @pytest.mark.parametrize("rec", RECEIPTS, ids=_ids())
    def test_line_amounts_actually_add_up(self, rec):
        """票上有小计栏时，Σ行金额必须等于它

        这不是判据，是**账**。对不上说明 truth.json 自己抄错了——
        语料写错的话，下面所有断言都是在拿错的基准测。

        票上根本没有小计栏的（MUJI）跳过：那里 subtotal 记 null 是
        如实记录，不是抄漏。
        """
        sub = rec["parsed"].get("subtotal")
        if sub is None:
            assert "没有小计栏" in rec["shape"], (
                f"{rec['id']} 的 subtotal 是 null，但票型里没写「票上没有这一栏」"
            )
            return
        line_sum = round(sum(i["amount"] for i in rec["parsed"]["items"]), 2)
        assert abs(line_sum - sub) <= 0.01, (
            f"{rec['id']} 的 truth.json 自己就平不了：Σ行 {line_sum} ≠ 小计 {sub}"
        )


class TestTheFailuresStayFailures:
    """反过来：改过的判据不许变得什么都放行"""

    def test_the_discounted_line_case_would_still_fail_without_the_discount_field(self):
        """「4.50 被抄成 8.4」这件事必须红

        8.4 是整张票的商品合计，被当成了某一行的单价。
        这类错正是过去漏掉的——不红就等于没校验。
        """
        rec = next(r for r in RECEIPTS if r["id"] == "hema_tea_drink")
        broken = json.loads(json.dumps(rec["parsed"]))
        broken["items"][1]["unit_price"] = 8.4
        assert verify({"parsed": broken})["line_arithmetic"] is False

    def test_the_no_subtotal_receipt_stays_readable(self):
        """没小计栏的票：字段缺失该红，不该被悄悄补 0"""
        rec = next(r for r in RECEIPTS if r["id"] == "noodle_shop_bill")
        broken = json.loads(json.dumps(rec["parsed"]))
        broken["subtotal"] = None
        v = verify({"parsed": broken})
        assert v["field_completeness"] is False
        assert v["sum_arithmetic"] is False


class TestRealCorpusEndToEnd:
    """真跑模型。默认跳过，要 `BEEOS_E2E=1` 才开——要凭证、要花钱。

    **不断言「字符抄对了吗」**，只断言机械层收不收。

    这是被逼出来的：写成逐字比对之后连着两次假红——
    「超盒算」被读成「超盒客」（上一次跑同一个模型读对了）、
    「六贤记 小河店」被读成「六贤记」（票上本来就是分两行印的）。
    字符级比对在这里就是个随机数发生器：它偶尔红，红了也没人能改，
    最后一定是被人注释掉。字符准不准是 definition 顶部声明的盲区，
    这里就不假装能验它。

    断言「机械层收不收」才稳，而且抓得住真东西：
    「合计被当成某一行单价」那类错会让 line_arithmetic 直接红，
    这正是要拦的。
    """

    @pytest.mark.skipif(
        not os.environ.get("BEEOS_E2E"),
        reason="真跑要花钱，设 BEEOS_E2E=1 才开",
    )
    @pytest.mark.parametrize("rec", RECEIPTS, ids=_ids())
    def test_the_box_verdicts_the_receipt_it_actually_read(self, rec):
        import base64

        from boxes.receipt.runner import _simulate_read_receipt

        raw = (CORPUS / rec["photo"]).read_bytes()
        url = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
        parsed = _simulate_read_receipt({"photo": url})["parsed"]
        got = verify({"parsed": parsed})

        failed = [k for k in RULES
                  if got[k] is False and rec["expect"][k] is not False]
        assert not failed, (
            f"{rec['id']}（{rec['shape']}）这张票机械层判不过：{failed}\n"
            f"  模型读成：{json.dumps(parsed.get('items'), ensure_ascii=False)}\n"
            f"  小计 {parsed.get('subtotal')!r} / 税 {parsed.get('tax')!r} / "
            f"优惠 {parsed.get('discount')!r} / 实付 {parsed.get('total')!r}\n"
            f"  票面真值见 tests/fixtures/receipts/truth.json 的 {rec['id']}"
        )


class TestRereadOnInconsistency:
    """抄完自查 → 对不上的行重问一次

    抽取腿是整套里唯一的模型环节，而它从来不回头看。同一个错连犯三次，
    全是「抓错了那个最显眼的数字」：规格里的 2 当数量、合计当单价、
    优惠当税。prompt 里写「请检查」换不来第二次注意力——
    读错单价那次它自认还挺自信，压根没往 low_confidence_fields 里填。
    把「哪几行对不上」算出来摊给它看，才是真的多给了它一次。
    """

    def _parsed(self, **over):
        base = json.loads(json.dumps(
            next(r for r in RECEIPTS if r["id"] == "hema_tea_drink")["parsed"]
        ))
        base.update(over)
        return base

    def test_a_consistent_parse_is_not_re_read(self, monkeypatch):
        """抄对了就不该多花钱——happy path 零额外调用"""
        from boxes.receipt import runner as receipt_runner

        calls = []
        monkeypatch.setattr(receipt_runner, "complete_json",
                            lambda *a, **kw: (calls.append(1), self._parsed())[1])
        receipt_runner._simulate_read_receipt({"photo": "data:image/jpeg;base64,eA=="})
        assert len(calls) == 1, f"抄对了还多调了一次模型：{len(calls)} 次"

    def test_an_inconsistent_line_triggers_exactly_one_reread(self, monkeypatch):
        from boxes.receipt import runner as receipt_runner

        self._parsed(items=[
            {"line_no": 1, "name": "脉动", "qty": 1, "unit_price": 3.9,
             "amount": 3.9, "discount_rate": None},
            # 第二行单价被抄成整张票的合计 8.4——真实踩过的坑
            {"line_no": 2, "name": "NB 无糖茉莉轻茶 1L", "qty": 1,
             "unit_price": 8.4, "amount": 4.5, "discount_rate": None},
        ])
        seen = []

        def fake(prompt, **kw):
            seen.append(prompt)
            if len(seen) == 1:
                return self._parsed(items=[
                    {"line_no": 1, "name": "脉动", "qty": 1, "unit_price": 3.9,
                     "amount": 3.9, "discount_rate": None},
                    {"line_no": 2, "name": "NB 无糖茉莉轻茶 1L", "qty": 1,
                     "unit_price": 8.4, "amount": 4.5, "discount_rate": None},
                ])
            return {"修正": [{"行号": 2, "单价": 4.5}], "low_confidence_fields": []}

        monkeypatch.setattr(receipt_runner, "complete_json", fake)
        out = receipt_runner._simulate_read_receipt({"photo": "data:image/jpeg;base64,eA=="})["parsed"]
        assert len(seen) == 2, f"应该只重问一次，实际调了 {len(seen)} 次"
        assert out["items"][1]["unit_price"] == 4.5, "重问的修正没合进来"

    def test_the_reread_is_told_which_lines_and_what_it_wrote(self, monkeypatch):
        """得把「你抄的是 8.4」摊给它看，空着手重问没用"""
        from boxes.receipt import runner as receipt_runner

        self._parsed(items=[{"line_no": 1, "name": "x", "qty": 1,
                             "unit_price": 8.4, "amount": 4.5, "discount_rate": None}])
        seen = []

        def fake(prompt, **kw):
            seen.append(prompt)
            if len(seen) == 1:
                return self._parsed(items=[{"line_no": 1, "name": "x", "qty": 1,
                                            "unit_price": 8.4, "amount": 4.5,
                                            "discount_rate": None}])
            return {"修正": [], "low_confidence_fields": []}

        monkeypatch.setattr(receipt_runner, "complete_json", fake)
        receipt_runner._simulate_read_receipt({"photo": "data:image/jpeg;base64,eA=="})
        assert len(seen) == 2 and "8.4" in seen[1], "重问没告诉模型它上一遍抄了什么"

    def test_it_never_loops(self, monkeypatch):
        """问一次就够。再错交给验收判据红——那是判据的活，不是抽取腿的"""
        from boxes.receipt import runner as receipt_runner

        n = {"i": 0}

        def fake(prompt, **kw):
            n["i"] += 1
            return self._parsed(items=[{"line_no": 1, "name": "x", "qty": 1,
                                        "unit_price": 8.4, "amount": 4.5,
                                        "discount_rate": None}])

        monkeypatch.setattr(receipt_runner, "complete_json", fake)
        receipt_runner._simulate_read_receipt({"photo": "data:image/jpeg;base64,eA=="})
        assert n["i"] == 2, f"来回调了 {n['i']} 次，应该封顶 2 次（一遍+一次重问）"

    def test_a_failed_reread_aborts_instead_of_returning_the_first_pass(self, monkeypatch):
        """退回第一遍 = 交一份我们自己都知道有问题、又没复核过的账

        那就是静默降级：看起来成功了，实际交出去的东西自己都说不清。
        中止是吵，但吵得对。
        """
        from boxes.receipt import runner as receipt_runner

        n = {"i": 0}

        def fake(prompt, **kw):
            n["i"] += 1
            if n["i"] == 1:
                return self._parsed(items=[{"line_no": 1, "name": "x", "qty": 1,
                                            "unit_price": 8.4, "amount": 4.5,
                                            "discount_rate": None}])
            raise RuntimeError("llm 502")

        monkeypatch.setattr(receipt_runner, "complete_json", fake)
        with pytest.raises(RuntimeError, match="llm 502"):
            receipt_runner._simulate_read_receipt({"photo": "data:image/jpeg;base64,eA=="})

    def test_the_reread_prompt_forbids_adjusting_numbers_to_fit(self):
        """看到「对不上」就改数去凑，是这套判据最怕的作弊"""
        from boxes.receipt.runner import _REREAD_PROMPT

        assert "不要" in _REREAD_PROMPT and "凑" in _REREAD_PROMPT, (
            "重问 prompt 没说「重新抄不是重新算」，模型很可能顺手把数改到对上"
        )

    def test_the_read_prompt_names_the_total_not_being_a_unit_price(self):
        """合计 / 订单总额 / 实付 三个数最显眼，最容易被抓去当某一行的单价"""
        from boxes.receipt.runner import _READ_PROMPT

        assert "商品合计" in _READ_PROMPT and "单价" in _READ_PROMPT, (
            "prompt 没点破「合计不是单价」——实测就是这么抄错的"
        )

    def test_the_read_prompt_says_to_use_low_confidence_fields(self):
        from boxes.receipt.runner import _READ_PROMPT

        assert "low_confidence_fields" in _READ_PROMPT


class TestDiscountAlreadyInTheSubtotal:
    """优惠**含在合计里**的票（六贤记）——不是每张票都从合计里减优惠

    实测那张票：
        奶皮鞭子  原价 8.80  实收单价 7.50  数量 2  小计 15.00
        合计:39.70(2.60)   折让金额:2.60   付款 39.70

    2.60 = 2 × (8.80 − 7.50) —— 折让是**奶皮鞭子那一行的差价**，
    而 39.70 已经等于 Σ行金额，折让含在里面。再减一次就成 37.10。
    """

    def _bakery(self, **over):
        rec = next(r for r in RECEIPTS if r["id"] == "liuxianji_bakery")
        base = json.loads(json.dumps(rec["parsed"]))
        base.update(over)
        return base

    def test_it_balances_now(self):
        """这张票之前是故意记红的，括号里那个数填上就该过"""
        assert verify({"parsed": self._bakery()})["total_arithmetic"] is True

    def test_the_old_formula_would_have_turned_it_red(self):
        """退回去减全额优惠就是 39.70 − 2.60 = 37.10 ≠ 39.70"""
        without = self._bakery()
        without["included_discount"] = None
        assert verify({"parsed": without})["total_arithmetic"] is False

    def test_a_discount_deducted_afterwards_still_works(self):
        """盒马那种另减的票不受影响——不是把优惠整个绕过去"""
        rec = next(r for r in RECEIPTS if r["id"] == "hema_soy_sauce")
        plain = json.loads(json.dumps(rec["parsed"]))
        plain["included_discount"] = None
        assert verify({"parsed": plain})["total_arithmetic"] is True

    def test_a_percentage_mistaken_for_an_amount_is_refused(self):
        """MUJI 的「实物总额(9%)」是税率不是金额，填进来不能把账算歪

        折让不可能比「折让金额」还多——included > discount 一定是读错了，
        这种当没含、照常减。
        这里 subtotal = 实付 = 290、discount = 0，本来就该平；
        要是那个 9 被当成金额用，账会算成 290 + 9 = 299 ≠ 290 而转红。
        **是守卫让它平的**，所以这条同时证明守卫真的在起作用。
        """
        rec = next(r for r in RECEIPTS if r["id"] == "muji_discounted")
        parsed = json.loads(json.dumps(rec["parsed"]))
        parsed["subtotal"] = 290.0
        parsed["included_discount"] = 9        # 把 9% 当成了金额
        parsed["discount"] = 0.0
        assert verify({"parsed": parsed})["total_arithmetic"] is True

    def test_a_genuinely_unbalanced_ticket_still_goes_red(self):
        """守卫只挡「含得比优惠还多」这种明显读错的，别把正常的账一起放过"""
        parsed = self._bakery(included_discount=None)
        assert verify({"parsed": parsed})["total_arithmetic"] is False

    def test_the_field_is_declared(self):
        fields = {f.name for f in
                  __import__("boxes.receipt", fromlist=["get_definition"]).get_definition()
                  .get_schema("schema_receipt_parsed").fields}
        assert "included_discount" in fields, (
            "schema 里没有 included_discount——括号里那个数就没有地方安放"
        )

    def test_the_prompt_names_the_real_pattern(self):
        from boxes.receipt.runner import _READ_PROMPT

        assert "39.70(2.60)" in _READ_PROMPT, (
            "prompt 里没有真实的「合计(括号)」写法，模型不知道该抄什么"
        )
        assert "折让金额" in _READ_PROMPT and "含在" in _READ_PROMPT, (
            "得说清「折让金额」和「含在合计里」的区别，否则模型分不清"
        )

    def test_the_prompt_warns_about_percentage_brackets(self):
        from boxes.receipt.runner import _READ_PROMPT

        assert "9%" in _READ_PROMPT, (
            "MUJI 票上有「实物总额(9%)」，不说清楚模型会把 9 当金额抄进来"
        )


class TestIncludedDiscountIsVisible:
    """算了就得画——上一轮的教训：折扣算进判据了但产出视图没画

    「算了却看不见」跟「声明了却拿不到」是同一族病。
    """

    def test_the_sum_row_says_it_is_included(self):
        """不标出来的话，用户拿 39.70 减 2.60 算出 37.10，以为盒子算错了"""
        from kanban.web import HTML_PAGE

        assert "含在合计里" in HTML_PAGE, (
            "优惠行没标「含在合计里」——用户会自己减一遍，然后觉得是盒子错了"
        )

    def test_the_marker_only_shows_when_there_is_one(self):
        from kanban.web import HTML_PAGE

        assert "r.included_discount ?" in HTML_PAGE, (
            "标记得由 included_discount 决定，不能一直摆着"
        )


class TestDateShapeIsNormalizedMechanically:
    """日期写法千姿百态，归一在机械层做——同类病第六次

    实测六贤记那张票印的是 `2026.10.10`。以前判据只认 `YYYY-MM-DD`，
    等于要求模型每次都乖乖转换。实测它不转：要么把整行
    `2026.10.10 20:36` 塞进 receipt_no 让日期空着，要么原样抄回来，
    两种都让 date_wellformed 红。产出一点错没有，是判据把
    「某一种日期形状」当成了所有日期的形状。
    """

    def _bakery(self, **over):
        rec = next(r for r in RECEIPTS if r["id"] == "liuxianji_bakery")
        base = json.loads(json.dumps(rec["parsed"]))
        base.update(over)
        return base

    def test_the_real_ticket_shape_is_no_longer_red(self):
        """票面印的写法就该过。这条转红过——就是这轮修的那个恒红"""
        assert verify({"parsed": self._bakery(receipt_date="2026.10.10")})[
            "date_wellformed"] is True

    def test_all_the_common_shapes_land_on_the_same_string(self):
        """归一的唯一目的：写法不同、同一天。认不出写法才算失败"""
        from boxes.receipt.runner import _normalize_date

        for raw in ("2026-10-10", "2026.10.10", "2026/10/10", "20261010",
                    "2026年10月10日", "26.10.10", "2026.10.10 20:36",
                    "2026/10/10T20:36", "2026.10.10(周二)"):
            assert _normalize_date(raw) == "2026-10-10", f"{raw!r} 没归一过来"

    def test_understanding_the_shape_is_not_approving_the_value(self):
        """归一只放宽写法，不放宽日期本身合不合法"""
        for raw in ("2026.13.45", "2026-02-30", "abc", "2026-10", "20:36", ""):
            r = verify({"parsed": self._bakery(receipt_date=raw)})
            assert r["date_wellformed"] is False, f"{raw!r} 不该被放过"
            assert r["totals_match"] is False, f"{raw!r} 过了 date 就等于整票放行"

    def test_the_normalized_value_is_what_gets_carried_out(self):
        """归一完要带出去——不然看板和产出里同一个日期长得不一样"""
        out = package(verify({"parsed": self._bakery(receipt_date="2026.10.10")}))
        assert out["receipt_date"] == "2026-10-10", (
            "产出里还是票面原样的 2026.10.10，对账的人得自己再换算一遍"
        )

    def test_the_two_digit_year_rule_is_written_down(self):
        """两位年是**加了个假设**，不是白捡的——假设得在代码里明写"""
        from boxes.receipt.runner import _normalize_date

        assert _normalize_date("26.10.10") == "2026-10-10"
        assert _normalize_date("98.10.10") == "1998-10-10"


class TestMissingRequiredFieldTriggersReread:
    """自查腿要查「漏抄」，不只查「抄错」

    上面那张六贤记实测：模型把 `2026.10.10 20:36` 整行塞进 receipt_no，
    receipt_date 空着。三行金额全对，所以行算术**一根手指都动不了**——
    盒子毫无反应，只能等验收闸红。而验收闸红的时候已经没什么可救的了。
    """

    def _parsed(self, **over):
        base = json.loads(json.dumps(
            next(r for r in RECEIPTS if r["id"] == "liuxianji_bakery")["parsed"]))
        base["receipt_date"] = ""      # 票上印着日期，这一遍读空了
        base["receipt_no"] = "202610101 010 20:36"   # 日期被塞进了单号
        base.update(over)
        return base

    def test_the_detector_sees_it(self):
        """纯机械，不花钱"""
        from boxes.receipt.runner import _missing_required_fields, _looks_inconsistent

        parsed = self._parsed()
        assert _looks_inconsistent(parsed) == [], (
            "这张票的金额全对，行算术本来就不该响——它查不到漏抄"
        )
        assert _missing_required_fields(parsed) == ["receipt_date"]

    def test_an_empty_date_triggers_exactly_one_reread(self, monkeypatch):
        from boxes.receipt import runner as receipt_runner

        seen = []

        def fake(prompt, **kw):
            seen.append(prompt)
            if len(seen) == 1:
                return self._parsed()
            return {"修正": [], "补字段": {"receipt_date": "2026-10-10"},
                    "low_confidence_fields": []}

        monkeypatch.setattr(receipt_runner, "complete_json", fake)
        out = receipt_runner._simulate_read_receipt(
            {"photo": "data:image/jpeg;base64,eA=="})["parsed"]
        assert len(seen) == 2, f"应该重问一次，实际调了 {len(seen)} 次"
        assert out["receipt_date"] == "2026-10-10", "重问补回来的日期没合进来"

    def test_both_problems_cost_one_call_not_two(self, monkeypatch):
        """算术不对 + 字段读空 → 还是只调一次

        「最多问一次」不变。拆成两次问，代价翻倍，
        还会让模型拿到两份互相矛盾的上下文。
        """
        from boxes.receipt import runner as receipt_runner

        seen = []

        def fake(prompt, **kw):
            seen.append(prompt)
            if len(seen) == 1:
                p = self._parsed()
                p["items"] = [{"line_no": 1, "name": "奶皮罐子", "qty": 2,
                               "unit_price": 8.8, "amount": 15.0,
                               "discount_rate": None}]
                return p
            return {"修正": [{"行号": 1, "单价": 7.5}],
                    "补字段": {"receipt_date": "2026-10-10"},
                    "low_confidence_fields": []}

        monkeypatch.setattr(receipt_runner, "complete_json", fake)
        out = receipt_runner._simulate_read_receipt(
            {"photo": "data:image/jpeg;base64,eA=="})["parsed"]
        assert len(seen) == 2, f"两件事应该共用一次重问，实际调了 {len(seen)} 次"
        assert out["items"][0]["unit_price"] == 7.5
        assert out["receipt_date"] == "2026-10-10"

    def test_a_still_empty_date_is_left_empty(self, monkeypatch):
        """第二遍还是空就保持空——让判据去红它

        硬塞一个占位值（比如今天日期）才是造假信号：
        「声明了却拿不到」那族病的老路。
        """
        from boxes.receipt import runner as receipt_runner

        n = {"i": 0}

        def fake(prompt, **kw):
            n["i"] += 1
            if n["i"] == 1:
                return self._parsed()
            return {"修正": [], "补字段": {"receipt_date": None},
                    "low_confidence_fields": ["receipt_date"]}

        monkeypatch.setattr(receipt_runner, "complete_json", fake)
        out = receipt_runner._simulate_read_receipt(
            {"photo": "data:image/jpeg;base64,eA=="})["parsed"]
        assert out["receipt_date"] == "", (
            "重问没读出来却塞了个值——这是在骗验收闸"
        )

    def test_a_populated_date_costs_nothing(self, monkeypatch):
        """happy path 零额外开销：日期有值就不该重问"""
        from boxes.receipt import runner as receipt_runner

        calls = []
        good = self._parsed(receipt_date="2026-10-10", receipt_no="NO.0016261010056172884")
        monkeypatch.setattr(receipt_runner, "complete_json",
                            lambda *a, **kw: (calls.append(1), good)[1])
        receipt_runner._simulate_read_receipt({"photo": "data:image/jpeg;base64,eA=="})
        assert len(calls) == 1, f"日期抄到了还多调了一次模型：{len(calls)} 次"

    def test_the_reread_prompt_names_the_real_mistake(self):
        """得把「日期塞进单号」这个具体的错摊给它看

        注意这条**不能只查词在不在**：`单号` 和 `receipt_date` 在输出格式
        示例里本来就有，只查词的话，指令整段被删掉它照样绿——实测过一次。
        查的是那句话本身。
        """
        from boxes.receipt.runner import _REREAD_PROMPT

        assert "两者别搞混" in _REREAD_PROMPT, (
            "重问 prompt 没点破「日期和单号别搞混」——这就是它这么抄错的原因"
        )
        assert "塞进单号" in _REREAD_PROMPT, (
            "光说别搞混不够，要把它上一遍到底干了什么摊出来"
        )

    def test_a_gutted_instruction_would_be_caught(self):
        """自证：把那段指令整段删掉，上面那条必须转红

        写完判据得证明自己会红。这条是为了让上面那条**有资格**当守卫——
        顺序是先写守卫、再删指令看它响不响，响完才算数。

        删法也有讲究：按**整段**删（从日期那条起、到下一条正文止），
        只删那一行的话后面三行补充说明还留着，守卫照样绿——第一次就踩了。
        """
        from boxes.receipt import runner as receipt_runner

        lines = receipt_runner._REREAD_PROMPT.splitlines()
        start = next(i for i, l in enumerate(lines) if l.startswith("- 日期抄票面"))
        end = next(i for i, l in enumerate(lines) if l.startswith("**最要紧的一条"))
        gutted = "\n".join(lines[:start] + ["- 日期随便写。", ""] + lines[end:])
        assert "两者别搞混" not in gutted and "塞进单号" not in gutted, (
            "删指令这步本身没生效，这个自证不算数"
        )
