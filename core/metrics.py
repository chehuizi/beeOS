"""
core.metrics - 把 definition.metrics 的声明和履约历史里的实测合成「指标读数」

为什么需要这一层：definition.metrics 里写的是 target（目标），
而目标不是事实。这一层负责把最近 N 次履约的真实信号聚合成 actual，
让看板显示的每个指标都有出处。

两个方向都得堵死，这是同一族病：
- 「声明了却没人消费」→ unwired() 点名，测试卡住
- 「消费了却没声明」   → 读数只从 definition.metrics 出发组装，
                        算法表里多出来的名字不会自己冒到看板上

所以这里不兜底：某个声明的指标没有采集算法，它不会被悄悄跳过，
而是被 unwired() 抓到，然后在测试里失败。

窗口 WINDOW 取最近 20 次：太小则噪声大（一次 LLM 抖动就让失败率翻倍），
太大则指标失去实时性。这是个偏保守的工程取值，不是业务语义。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from core.models import BeeBoxDefinition, MetricDef


# 采集窗口：最近 N 次履约
WINDOW = 20

# 采集算法签名：吃最近 N 条履约记录，回「实测值 + 参与统计的样本数」
# 样本数由算法自己报，不能由外面猜——有些指标只认已完成的履约，
# 有些只认已判定的履约，猜出来的样本数是假数字。
# value 为 None 表示样本不足：这不是 0，是「还没测过」。
Computer = Callable[[list[dict[str, Any]]], "Measurement"]

# (value, sample_size)
Measurement = tuple[Optional[float], int]


def declared(definition: BeeBoxDefinition) -> list[MetricDef]:
    """盒子声明的全部指标（质量 + 延迟）"""
    return list(definition.metrics.quality) + list(definition.metrics.latency)


def unwired(definition: BeeBoxDefinition, computers: dict[str, Computer]) -> list[str]:
    """声明了但没有采集算法的指标名

    这是「声明了却没人消费」的检测口。正常情况下应该永远返回空列表；
    非空就说明有人在 definition.metrics 里加了一个没人算的指标。
    测试里断言它为空。
    """
    names = {m.name for m in declared(definition)}
    return sorted(names - set(computers))


def orphan_computers(definition: BeeBoxDefinition, computers: dict[str, Computer]) -> list[str]:
    """有采集算法但没在 definition 里声明的指标名

    「消费了却没声明」的反向检测：算法表里躺着一个名字，definition 忘了写，
    它就永远不会出现在看板上——一样是拿不到。
    """
    names = {m.name for m in declared(definition)}
    return sorted(set(computers) - names)


def recent_records(records: list[dict[str, Any]], window: int = WINDOW) -> list[dict[str, Any]]:
    """最近 window 条履约记录，按时间正序返回"""
    ordered = sorted(records, key=lambda r: r.get("created_at") or "", reverse=True)
    return list(reversed(ordered[:window]))


def measure(
    definition: BeeBoxDefinition,
    records: list[dict[str, Any]],
    computers: dict[str, Computer],
    *,
    window: int = WINDOW,
) -> list[dict[str, Any]]:
    """把声明的指标和历史实测合成看板可渲染的读数

    Returns:
        [{name, definition, target, unit, actual, sample_size, window, at}]
        actual 为 None 表示窗口内没有足够样本——这不是 0，
        是「还没测过」，看板上两者必须长得不一样。
    """
    sample = recent_records(records, window)
    readings: list[dict[str, Any]] = []

    for metric in declared(definition):
        computer = computers.get(metric.name)
        if computer is None:
            continue  # 不在读数里出现；由 unwired() 负责点名报错
        actual, sample_size = computer(sample)
        readings.append({
            "name": metric.name,
            "definition": metric.definition,
            "target": metric.target,
            "unit": metric.unit,
            "direction": metric.direction,
            "actual": actual,
            "sample_size": sample_size,
            "window": window,
            "at": sample[-1].get("created_at") if sample else None,
        })

    return readings


def ratio_pct(records: list[dict[str, Any]], predicate) -> Measurement:
    """满足 predicate 的记录占全部记录的比例（百分数），无记录时 (None, 0)"""
    if not records:
        return None, 0
    hits = sum(1 for r in records if predicate(r))
    return round(hits / len(records) * 100.0, 1), len(records)


def mean(samples: list[float]) -> Measurement:
    if not samples:
        return None, 0
    # 保留 3 位：亚毫秒级的耗时 round 到 2 位会变成 0.0，
    # 一个「没测到时间」的 0 和一个「真的是零」在数据层就分不开了。
    # 显示精度交给前端按单位决定，后端只管不丢信息。
    return round(sum(samples) / len(samples), 3), len(samples)


def percentile(samples: list[float], pct: float) -> Measurement:
    """百分位（线性插值），无样本时 (None, 0)"""
    if not samples:
        return None, 0
    ordered = sorted(samples)
    if len(ordered) == 1:
        return round(ordered[0], 3), 1
    pos = (len(ordered) - 1) * pct
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (pos - low), 3), len(ordered)