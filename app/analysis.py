"""累计暴露分析引擎。

核心思想：接收方"已经拿到过什么"构成知识库。即使某次发布后来被撤回，
数据一旦发出即视为已暴露，撤回只阻止继续下载，不从知识库中删除。

检测三类信号（都是描述性信号，不是匿名保证）：
1. singleton —— 新文件自身的等价类只有 1 人；
2. small     —— 新文件自身等价类不超过策略 small_threshold；
3. shrunk    —— 新文件的细粒度组合落在历史某个粗粒度桶内，且人数更少，
                 即新文件缩小了接收方原本掌握的群体范围。
"""
from __future__ import annotations

from . import qi, treatment


class AnalysisError(ValueError):
    pass


def build_knowledge(ledger_entries: list[dict]) -> dict:
    """从账本条目构建某接收方的累计知识库。

    包含已撤回发布（status == withdrawn）：撤回不抹除已暴露事实。
    拒发条目没有制品行，自然不贡献行。
    """
    flat_rows = []          # 每行：release_id/period/status/qi_display/person_id
    release_meta = {}       # release_id -> 元信息
    periods_seen = set()
    for entry in ledger_entries:
        rid = entry["release_id"]
        artifact = entry.get("artifact") or {}
        art_rows = artifact.get("rows", [])
        # 拒发条目没有制品行：登记元信息便于审计追溯，但不贡献任何已暴露行
        release_meta[rid] = {
            "release_id": rid,
            "period": entry["period"],
            "status": entry["status"],
            "created_at": entry["created_at"],
            "has_artifact": bool(art_rows),
        }
        if not art_rows:
            continue
        person_ids = (entry.get("internal") or {}).get("row_person_ids", [])
        for idx, arow in enumerate(art_rows):
            pid = person_ids[idx] if idx < len(person_ids) else f"{rid}#row{idx}"
            flat_rows.append({
                "release_id": rid,
                "period": entry["period"],
                "status": entry["status"],
                "qi_display": arow["qi_display"],
                "person_id": pid,
            })
            periods_seen.add(entry["period"])
    return {
        "rows": flat_rows,
        "release_meta": release_meta,
        "periods": sorted(periods_seen),
    }


def _groups_by_release(knowledge: dict) -> dict:
    """每个历史发布内按 QI 组合分组：release_id -> {key_tuple: [row,...]}"""
    out: dict[str, dict[tuple, list[dict]]] = {}
    for row in knowledge["rows"]:
        rid = row["release_id"]
        key = tuple(row["qi_display"][f] for f in qi.QI_FIELDS)
        out.setdefault(rid, {}).setdefault(key, []).append(row)
    return out


def _compatible_keys(cand_key: tuple, prior_key: tuple) -> bool:
    return all(qi.compatible(f, a, b) for f, a, b in zip(qi.QI_FIELDS, cand_key, prior_key))


def analyze(candidate: dict, plan: dict, knowledge: dict,
            small_threshold: int = 2) -> dict:
    """对候选任务应用方案后，与累计知识库做真实比较。"""
    plan = treatment.validate_plan(plan)
    rows = candidate["rows"]
    applied = treatment.apply_plan(rows, plan)
    loss = treatment.measure_loss(rows, applied, plan)

    released = applied["released"]
    # 候选等价类（按处置后的展示值分组）
    cand_groups: dict[tuple, list[dict]] = {}
    for r in released:
        key = tuple(r["qi_display"][f] for f in qi.QI_FIELDS)
        cand_groups.setdefault(key, []).append(r)

    prior_groups = _groups_by_release(knowledge)
    periods = set(knowledge["periods"])
    late_arrival = bool(periods) and candidate["period"] < max(periods)

    classes = []
    flagged_persons: set[str] = set()
    n_singleton = n_small = n_shrunk = 0

    for key, members in sorted(cand_groups.items()):
        cand_size = len(members)
        cand_pids = [m["person_id"] for m in members]

        prior_matches, shrink_details = [], []
        cumulative_pids = set(cand_pids)
        prior_sizes = []

        for rid, groups in prior_groups.items():
            for pkey, prows in groups.items():
                if not _compatible_keys(key, pkey):
                    continue
                p_size = len(prows)
                prior_sizes.append(p_size)
                meta = knowledge["release_meta"][rid]
                match = {
                    "release_id": rid,
                    "period": meta["period"],
                    "status": meta["status"],
                    "prior_group_key": list(pkey),
                    "prior_group_size": p_size,
                }
                prior_matches.append(match)
                for pr in prows:
                    cumulative_pids.add(pr["person_id"])
                if p_size > cand_size:
                    shrink_details.append({
                        "release_id": rid,
                        "period": meta["period"],
                        "status": meta["status"],
                        "prior_size": p_size,
                        "candidate_size": cand_size,
                    })

        flags, flag_labels = [], []
        if cand_size == 1:
            flags.append("singleton")
            flag_labels.append("新文件内仅 1 人的组合")
            n_singleton += 1
        if cand_size <= small_threshold:
            flags.append("small")
            flag_labels.append(f"小群体（≤{small_threshold} 人，策略触发线，非匿名保证）")
            n_small += 1
        if shrink_details:
            flags.append("shrunk")
            names = ", ".join(
                f"{d['release_id']}({d['period']}"
                + ("，已撤回但仍计入已暴露" if d["status"] == "withdrawn" else "")
                + f"):{d['prior_size']}→{d['candidate_size']}"
                for d in shrink_details
            )
            flag_labels.append(f"新文件缩小了历史群体：{names}")
            n_shrunk += 1
        if flags:
            flagged_persons.update(cand_pids)

        classes.append({
            "key": {f: v for f, v in zip(qi.QI_FIELDS, key)},
            "key_tuple": list(key),
            "candidate_size": cand_size,
            "candidate_person_ids": cand_pids,
            "members": [
                {
                    "person_id": m["person_id"],
                    "qi_raw": m.get("qi_raw", {f: str(m[f]) for f in qi.QI_FIELDS}),
                    "salary": m.get("salary"),
                    "diagnosis": m.get("diagnosis"),
                }
                for m in members
            ],
            "prior_matches": prior_matches,
            "prior_min_size": min(prior_sizes) if prior_sizes else None,
            "shrunk": bool(shrink_details),
            "shrink_details": shrink_details,
            "cumulative_person_count": len(cumulative_pids),
            "flags": flags,
            "flag_labels": flag_labels,
        })

    # 跨时间段重复出现的人（内部 linkage 信号；person_id 为虚构编号）
    person_periods: dict[str, set] = {}
    for pr in knowledge["rows"]:
        person_periods.setdefault(pr["person_id"], set()).add(pr["period"])
    for r in released:
        person_periods.setdefault(r["person_id"], set()).add(candidate["period"])
    repeated = sorted(p for p, ps in person_periods.items() if len(ps) >= 2)

    summary = {
        "candidate_rows": len(rows),
        "released_rows": len(released),
        "suppressed_rows": len(applied["suppressed"]),
        "equivalence_classes": len(classes),
        "singleton_classes": n_singleton,
        "small_classes": n_small,
        "shrunk_classes": n_shrunk,
        "flagged_person_count": len(flagged_persons),
        "flagged_person_ids": sorted(flagged_persons),
        "cross_period_repeated_persons": len(repeated),
        "cross_period_repeated_person_ids": repeated,
        "history_release_count": len(knowledge["release_meta"]),
        "history_withdrawn_count": sum(
            1 for m in knowledge["release_meta"].values() if m["status"] == "withdrawn"
        ),
        "late_arrival": late_arrival,
    }

    return {
        "candidate_id": candidate["candidate_id"],
        "recipient_id": candidate["recipient_id"],
        "period": candidate["period"],
        "late_arrival": late_arrival,
        "small_threshold": small_threshold,
        "plan": plan,
        "classes": classes,
        "summary": summary,
        "loss": loss,
        "row_diffs": applied["row_diffs"],
        "applied_counts": {
            "released": len(released),
            "suppressed": len(applied["suppressed"]),
        },
    }


def preset_analyses(candidate: dict, knowledge: dict, small_threshold: int = 2) -> list[dict]:
    """四个预设方案的分析结果，供方案比较。

    auto-suppress 需要先跑一遍原样分析拿到被标记人员，故顺序执行。
    """
    baseline = analyze(candidate, treatment.plan_passthrough(), knowledge, small_threshold)
    flagged = baseline["summary"]["flagged_person_ids"]

    presets = [
        ("passthrough", "原样发布（不处置，仅作基线对照）", treatment.plan_passthrough()),
        ("suppress", "抑制全部被标记行", treatment.plan_suppress(flagged)),
        ("bucket", "分桶泛化（年龄/邮编/职业）", treatment.plan_bucket()),
        ("reject", "整份拒发", treatment.plan_reject()),
    ]
    results = []
    for code, label, plan in presets:
        if code == "passthrough":
            res = baseline
        else:
            res = analyze(candidate, plan, knowledge, small_threshold)
        res["preset_code"] = code
        res["preset_label"] = label
        results.append(res)
    return results
