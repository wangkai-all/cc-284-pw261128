"""处置方案与信息损失度量。

三种处置：
1. 抑制（suppression）：整行不发布，按人标记；
2. 分桶（bucketing）：提高一个或多个 QI 字段的泛化层级，可与抑制组合；
3. 拒发（rejection）：整个候选文件不发布，信息损失为 100%。

信息损失是多指标描述性度量，用于方案间比较，不构成任何匿名保证。
"""
from __future__ import annotations

from . import qi

SENSITIVE_FIELDS = ["salary", "diagnosis"]

# 综合损失的权重（仅用于方案排序，是人为设定的效用权重，不是隐私阈值）
LOSS_WEIGHTS = {
    "row_suppression": 0.40,
    "generalization": 0.30,
    "salary_shift": 0.15,
    "diagnosis_tvd": 0.15,
}

# 分桶方案的默认建议层级
BUCKET_LEVELS = {"age": 2, "gender": 0, "zip": 2, "occupation": 1}


class PlanError(ValueError):
    """处置方案非法。"""


def validate_plan(plan: dict) -> dict:
    """校验并归一化方案。"""
    if not isinstance(plan, dict):
        raise PlanError("方案必须是对象")
    rejected = bool(plan.get("rejected", False))
    raw_levels = plan.get("levels", {}) or {}
    levels = {}
    for f in qi.QI_FIELDS:
        lvl = int(raw_levels.get(f, 0))
        try:
            qi._check_level(f, lvl)  # 非法时抛 QIError（ValueError 子类）
        except qi.QIError as exc:
            raise PlanError(str(exc)) from exc
        levels[f] = lvl
    suppressed = plan.get("suppressed_person_ids", []) or []
    if not isinstance(suppressed, list) or not all(isinstance(p, str) for p in suppressed):
        raise PlanError("suppressed_person_ids 必须是字符串数组")
    return {
        "rejected": rejected,
        "levels": levels,
        "suppressed_person_ids": list(dict.fromkeys(suppressed)),  # 去重保序
    }


def plan_passthrough() -> dict:
    return {"rejected": False, "levels": qi.default_levels(), "suppressed_person_ids": []}


def plan_reject() -> dict:
    return {"rejected": True, "levels": qi.default_levels(), "suppressed_person_ids": []}


def plan_suppress(flagged_person_ids, levels: dict | None = None) -> dict:
    return {
        "rejected": False,
        "levels": levels or qi.default_levels(),
        "suppressed_person_ids": list(flagged_person_ids),
    }


def plan_bucket(levels: dict | None = None, extra_suppressed=None) -> dict:
    merged = qi.default_levels()
    merged.update(levels or BUCKET_LEVELS)
    return {
        "rejected": False,
        "levels": merged,
        "suppressed_person_ids": list(extra_suppressed or []),
    }


def apply_plan(rows: list[dict], plan: dict) -> dict:
    """把方案应用到候选行，返回处置后的行与逐行差异。

    返回：
    {
      "released": [ {person_id, qi_display, qi_raw, salary, diagnosis, ...}, ... ],
      "suppressed": [ 同结构（原始行） ],
      "row_diffs": [ {person_id, action, before: {f: v}, after: {f: v}}, ... ],
    }
    """
    plan = validate_plan(plan)
    suppressed_set = set(plan["suppressed_person_ids"])
    released, suppressed, row_diffs = [], [], []

    if plan["rejected"]:
        for row in rows:
            row_diffs.append({
                "person_id": row["person_id"],
                "action": "rejected",
                "before": {f: str(row[f]) for f in qi.QI_FIELDS},
                "after": {f: None for f in qi.QI_FIELDS},
            })
        return {"released": [], "suppressed": list(rows), "row_diffs": row_diffs}

    for row in rows:
        pid = row["person_id"]
        before = {f: str(row[f]) for f in qi.QI_FIELDS}
        if pid in suppressed_set:
            suppressed.append(row)
            row_diffs.append({
                "person_id": pid, "action": "suppressed",
                "before": before, "after": {f: None for f in qi.QI_FIELDS},
            })
            continue
        display = qi.generalize_row(row, plan["levels"])
        treated = dict(row)
        treated["qi_display"] = display
        treated["qi_raw"] = before
        released.append(treated)
        after_changes = {f: display[f] for f in qi.QI_FIELDS if display[f] != before[f]}
        row_diffs.append({
            "person_id": pid,
            "action": "generalized" if after_changes else "unchanged",
            "before": before,
            "after": after_changes,  # 仅列出被桶化的字段
        })

    return {"released": released, "suppressed": suppressed, "row_diffs": row_diffs}


def _cell_loss(field: str, display: str) -> float:
    """单个泛化单元的损失：覆盖比例，0 为原值，1 为整域。"""
    domain_size = len(qi.DOMAINS[field])
    covered = len(qi.covered_raw(field, display))
    return (covered - 1) / (domain_size - 1) if domain_size > 1 else 0.0


def _salary_mean(rows: list[dict]):
    vals = [r["salary"] for r in rows if isinstance(r.get("salary"), (int, float))]
    return sum(vals) / len(vals) if vals else None


def _diagnosis_dist(rows: list[dict]) -> dict:
    dist: dict[str, float] = {}
    for r in rows:
        d = r.get("diagnosis")
        if d is not None:
            dist[d] = dist.get(d, 0.0) + 1.0
    total = sum(dist.values())
    return {k: v / total for k, v in dist.items()} if total else {}


def measure_loss(rows: list[dict], applied: dict, plan: dict) -> dict:
    """计算信息损失报告。"""
    plan = validate_plan(plan)
    n_total = len(rows)
    released = applied["released"]
    n_released = len(released)
    n_suppressed = len(applied["suppressed"])

    if plan["rejected"]:
        return {
            "rejected": True,
            "released_count": 0,
            "suppressed_count": n_total,
            "row_suppression_rate": 1.0,
            "generalization_loss": 1.0,
            "salary_mean_original": _salary_mean(rows),
            "salary_mean_released": None,
            "salary_mean_shift": 1.0,
            "diagnosis_tvd": 1.0,
            "overall_loss": 1.0,
            "utility_score": 0.0,
            "weights": LOSS_WEIGHTS,
            "note": "拒发：全部行不发布，效用为零，信息损失 100%。",
        }

    row_rate = n_suppressed / n_total if n_total else 0.0

    if n_released:
        gen_values = [r["qi_display"][f] for r in released for f in qi.QI_FIELDS]
        gen_loss = sum(_cell_loss(f, r["qi_display"][f])
                       for r in released for f in qi.QI_FIELDS) / (n_released * len(qi.QI_FIELDS))
    else:
        gen_loss = 1.0

    mean_orig = _salary_mean(rows)
    mean_rel = _salary_mean(released)
    if mean_orig and mean_rel is not None:
        salary_shift = abs(mean_rel - mean_orig) / mean_orig
    else:
        salary_shift = 1.0 if n_released == 0 else 0.0

    d_orig, d_rel = _diagnosis_dist(rows), _diagnosis_dist(released)
    diagnosis_tvd = 0.5 * sum(
        abs(d_orig.get(k, 0.0) - d_rel.get(k, 0.0))
        for k in set(d_orig) | set(d_rel)
    ) if n_released else 1.0

    overall = (
        LOSS_WEIGHTS["row_suppression"] * row_rate
        + LOSS_WEIGHTS["generalization"] * gen_loss
        + LOSS_WEIGHTS["salary_shift"] * salary_shift
        + LOSS_WEIGHTS["diagnosis_tvd"] * diagnosis_tvd
    )
    return {
        "rejected": False,
        "released_count": n_released,
        "suppressed_count": n_suppressed,
        "row_suppression_rate": round(row_rate, 4),
        "generalization_loss": round(gen_loss, 4),
        "salary_mean_original": round(mean_orig, 1) if mean_orig is not None else None,
        "salary_mean_released": round(mean_rel, 1) if mean_rel is not None else None,
        "salary_mean_shift": round(salary_shift, 4),
        "diagnosis_tvd": round(diagnosis_tvd, 4),
        "overall_loss": round(overall, 4),
        "utility_score": round(1.0 - overall, 4),
        "weights": LOSS_WEIGHTS,
        "note": "损失权重为人为设定的效用比较参数；任何方案都不能仅凭群体大小阈值声称绝对匿名。",
    }
