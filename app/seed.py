"""全虚构样例数据。

所有人名、编号、属性均为程序生成的虚构数据，与任何真实个人无关。
手工编排了稀有 QI 组合，使候选文件与历史发布之间能真实出现
"粗桶 → 细粒度单人"的群体缩小链路，用于演示检测逻辑。
"""
from __future__ import annotations

import copy
import random

from . import treatment

PERIODS = ["2025H1", "2025H2", "2026H1", "2026H2"]
RECIPIENT_A = "RC-医保研究合作方"
RECIPIENT_B = "RC-区域健康规划院"

SALARY_BANDS = [4200, 5300, 6100, 7800, 9200, 11500, 13800]
DIAGNOSES = ["高血压", "2型糖尿病", "支气管炎", "腰椎间盘突出",
             "缺铁性贫血", "偏头痛", "过敏性鼻炎", "体检无异常"]

# 固定人员花名册：person_id 是内部稳定假名编号，永不进入对外制品
_POOL = [
    # id, 年龄, 性别, 邮编, 职业
    ("P001", 34, "男", "10009", "软件工程师"),
    ("P002", 41, "女", "10012", "软件工程师"),
    ("P003", 29, "男", "20013", "软件工程师"),
    ("P004", 38, "女", "10001", "数据分析师"),
    ("P005", 47, "男", "31011", "土木工程师"),
    ("P006", 52, "女", "20005", "医生"),
    ("P007", 36, "男", "20020", "医生"),
    ("P008", 44, "女", "31002", "护士"),
    ("P009", 31, "女", "51003", "护士"),
    ("P010", 58, "男", "10023", "药剂师"),
    ("P011", 49, "女", "10009", "教师"),
    ("P012", 33, "男", "61004", "教师"),
    ("P013", 45, "女", "20013", "研究员"),
    ("P014", 27, "男", "31025", "销售"),
    ("P015", 39, "女", "20005", "会计"),
    ("P016", 54, "男", "51018", "行政助理"),
    ("P017", 30, "女", "61017", "厨师"),
    ("P018", 42, "男", "10012", "物流专员"),
    ("P019", 35, "女", "10001", "硬件工程师"),
    ("P020", 50, "男", "31011", "行政助理"),
    # 手工编排的稀有组合人员
    # P041（36岁）：2025H2 分桶发布时与 P001(34岁) 同处
    #   (40-49? 否, 35-39段, 男, 10***, 工程技术类) 桶，桶内 2 人；
    #   2026H2 候选以原始职业出现且该组合仅 1 人，触发 shrunk（2→1）。
    ("P041", 36, "男", "10009", "软件工程师"),
    # P042：罕见邮编+职业组合，跨时间段重复出现
    ("P042", 57, "女", "61017", "药剂师"),
]

# 各时间段参与数据采集的人员（P041/P042 跨期重复，制造 linkage 条件）
_PARTICIPATION = {
    "2025H1": ["P001", "P002", "P004", "P006", "P008", "P011", "P014",
               "P015", "P017", "P019"],
    "2025H2": ["P001", "P002", "P003", "P004", "P005", "P007", "P009",
               "P010", "P012", "P013", "P016", "P018", "P020", "P041", "P042"],
    # 2026H1 已发布子集（分桶）
    "2026H1_PUBLISHED": ["P002", "P006", "P011", "P015"],
    # 2026H2 主候选（含 P041/P042 的稀有组合，是主审查任务）
    "2026H2": ["P002", "P004", "P006", "P011", "P015", "P018", "P019",
               "P041", "P042"],
    # 2025H2 迟到补表（2026-10 才送达，数据期早于账面最新发布期 2026H1）
    # P011 同时出现在 2026H1 发布中，保证迟到任务与"送达后才产生的发布"存在真实可比组合
    "2025H2_LATE": ["P003", "P007", "P011", "P012", "P014", "P020"],
}

_PERIOD_AGE_OFFSET = {"2025H1": 0, "2025H2": 0, "2025H2_LATE": 0,
                      "2026H1_PUBLISHED": 1, "2026H2": 1}
_TS = {
    "2025H1": "2025-08-12T10:20:00",
    "2025H2": "2026-03-09T15:40:00",
    "2026H1_PUBLISHED": "2026-09-20T16:10:00",
    "2026H2": "2026-10-28T09:05:00",
    "2025H2_LATE": "2026-10-30T11:30:00",
}


def _person_map() -> dict:
    rng = random.Random(20261120)
    pmap = {}
    for pid, age, gender, zipc, occ in _POOL:
        pmap[pid] = {
            "person_id": pid,
            "age": age,
            "gender": gender,
            "zip": zipc,
            "occupation": occ,
            "salary": SALARY_BANDS[rng.randrange(len(SALARY_BANDS))],
            "diagnosis": DIAGNOSES[rng.randrange(len(DIAGNOSES))],
        }
    return pmap


def generate_rows(participation_key: str) -> list[dict]:
    """按采集名册生成行（虚构）。"""
    pmap = _person_map()
    offset = _PERIOD_AGE_OFFSET[participation_key]
    rows = []
    for pid in _PARTICIPATION[participation_key]:
        row = copy.deepcopy(pmap[pid])
        row["age"] = row["age"] + offset
        rows.append(row)
    # 虚构情形：迟到补表归档时按最新档案校正了 P011 的年龄，
    # 使其与 2026H1 已发布桶在全部 QI 维度相容（用于演示跨期真实可比）
    if participation_key == "2025H2_LATE":
        for row in rows:
            if row["person_id"] == "P011":
                row["age"] = 50
    return rows


def _candidate(cand_id: str, period: str, recipient_id: str, title: str,
               rows_key: str, arrived_at: str, note: str = "",
               policy_at_arrival: int = 3) -> dict:
    return {
        "candidate_id": cand_id,
        "period": period,
        "recipient_id": recipient_id,
        "title": title,
        "source_file": f"{period}_{recipient_id}_deidentified.json",
        "arrived_at": arrived_at,
        "note": note,
        "status": "pending",
        "policy_version_at_arrival": policy_at_arrival,
        "rows": generate_rows(rows_key),
    }


def _artifact(rows: list[dict], plan: dict) -> tuple[dict, list[str]]:
    """构造对外发布制品（不含 person_id）。"""
    applied = treatment.apply_plan(rows, plan)
    out_rows = [
        {
            "qi_display": r["qi_display"],
            "salary": r["salary"],
            "diagnosis": r["diagnosis"],
        }
        for r in applied["released"]
    ]
    return (
        {
            "format": "deid-export/v1",
            "generalization_levels": plan["levels"],
            "suppressed_count": len(applied["suppressed"]),
            "rows": out_rows,
        },
        [r["person_id"] for r in applied["released"]],
    )


def build_seed_state() -> dict:
    """初始状态：3 条历史发布（含一次拒发）+ 常规/迟到候选 + 新接收方对照。"""
    h1_rows = generate_rows("2025H1")
    h2_rows = generate_rows("2025H2")
    h1p_rows = generate_rows("2026H1_PUBLISHED")

    art1, ids1 = _artifact(h1_rows, treatment.plan_passthrough())
    art2, ids2 = _artifact(h2_rows, treatment.plan_bucket())
    art3, ids3 = _artifact(h1p_rows, treatment.plan_bucket())

    releases = [
        {
            "release_id": "REL-2025-001",
            "candidate_id": "CAND-H1-SEED",
            "recipient_id": RECIPIENT_A,
            "period": "2025H1",
            "status": "released",
            "created_at": _TS["2025H1"],
            "plan": treatment.plan_passthrough(),
            "policy_version": 1,
            "artifact": art1,
            "internal": {"row_person_ids": ids1},
            "withdrawn_at": None,
            "withdraw_reason": None,
            "note": "首次发布，当时无历史暴露可比。",
        },
        {
            "release_id": "REL-2025-002",
            "candidate_id": "CAND-H2-SEED",
            "recipient_id": RECIPIENT_A,
            "period": "2025H2",
            "status": "released",
            "created_at": _TS["2025H2"],
            "plan": treatment.plan_bucket(),
            "policy_version": 2,
            "artifact": art2,
            "internal": {"row_person_ids": ids2},
            "withdrawn_at": None,
            "withdraw_reason": None,
            "note": "按当时策略分桶（年龄10岁段/邮编前2位/职业大类）。",
        },
        {
            "release_id": "REL-2025-R01",
            "candidate_id": "CAND-REJ-SEED",
            "recipient_id": RECIPIENT_A,
            "period": "2025H2",
            "status": "rejected",
            "created_at": _TS["2025H2"],
            "plan": treatment.plan_reject(),
            "policy_version": 2,
            "artifact": None,
            "internal": {"row_person_ids": []},
            "withdrawn_at": None,
            "withdraw_reason": None,
            "note": "同批次补充表经审查拒发：敏感字段分布畸变且存在单人组合。",
        },
        {
            "release_id": "REL-2026-001",
            "candidate_id": "CAND-H1P-SEED",
            "recipient_id": RECIPIENT_A,
            "period": "2026H1",
            "status": "released",
            "created_at": _TS["2026H1_PUBLISHED"],
            "plan": treatment.plan_bucket(),
            "policy_version": 3,
            "artifact": art3,
            "internal": {"row_person_ids": ids3},
            "withdrawn_at": None,
            "withdraw_reason": None,
            "note": "2026 上半年先发布的子集；晚到的 2025H2 补表必须与它叠加审查。",
        },
    ]

    candidates = [
        # 主审查任务：P041 的 2→1 缩小链路在此体现
        _candidate(
            "CAND-2026H2-A", "2026H2", RECIPIENT_A,
            "2026下半年去标识就诊数据集", "2026H2", _TS["2026H2"],
            "重点核查与 REL-2025-002 的稀有组合叠加（P041 所在粗桶→单人组合）。",
        ),
        # 迟到任务：数据期 2025H2 早于账面最新发布期 2026H1
        _candidate(
            "CAND-2025H2-A-LATE", "2025H2", RECIPIENT_A,
            "迟到的 2025 下半年补充数据集", "2025H2_LATE", _TS["2025H2_LATE"],
            "旧时间段任务迟到：必须按当前策略 v3 与全部历史暴露重新审查，"
            "不能按送达前的旧处置直接放行。",
        ),
        # 新接收方：知识库为空，作为对照
        _candidate(
            "CAND-2026H1-B", "2026H1", RECIPIENT_B,
            "2026上半年去标识就诊数据集（新接收方）", "2026H1_PUBLISHED",
            _TS["2026H1_PUBLISHED"],
            "该接收方无历史发布，知识库为空，便于对照。",
        ),
        _candidate(
            "CAND-2026H2-B", "2026H2", RECIPIENT_B,
            "2026下半年去标识就诊数据集（新接收方）", "2026H2",
            _TS["2026H2"],
        ),
    ]

    return {
        "candidates": candidates,
        "releases": releases,
        "policy": {
            "version": 3,
            "small_threshold": 2,
            "updated_at": _TS["2026H1_PUBLISHED"],
            "history": [
                {"version": 1, "change": "初始策略：原样发布。", "at": _TS["2025H1"]},
                {"version": 2, "change": "启用分桶与小群体审查线（阈值仅触发审查，不保证匿名）。",
                 "at": _TS["2025H2"]},
                {"version": 3, "change": "新增跨发布群体缩小检测与迟到任务强制重审。",
                 "at": _TS["2026H1_PUBLISHED"]},
            ],
        },
        "audit_log": [
            {"at": _TS["2025H1"], "action": "publish", "release_id": "REL-2025-001"},
            {"at": _TS["2025H2"], "action": "publish", "release_id": "REL-2025-002"},
            {"at": _TS["2025H2"], "action": "reject", "release_id": "REL-2025-R01"},
            {"at": _TS["2026H1_PUBLISHED"], "action": "publish", "release_id": "REL-2026-001"},
        ],
    }


def seed_summary() -> dict:
    """供首页展示的样例编排说明。"""
    return {
        "persons": len(_POOL),
        "periods": PERIODS,
        "recipients": [RECIPIENT_A, RECIPIENT_B],
        "rare_links": [
            {
                "person_id": "P041",
                "story": ("2025H2 以分桶形式发布：(35-39, 男, 10***, 工程技术类) 桶内有 "
                          "P001、P041 共 2 人；2026H2 候选中 P041 以原始职业出现且该组合仅 "
                          "1 人，新文件把接收方掌握的群体从 2 人缩小到 1 人（shrunk 2→1）。"),
            },
            {
                "person_id": "P042",
                "story": "罕见邮编 61017 + 药剂师组合在多个时间段重复出现，"
                         "便于观察跨时间段 linkage 信号。",
            },
        ],
        "late_arrival": {
            "candidate_id": "CAND-2025H2-A-LATE",
            "story": ("2025H2 数据期的补表 2026-10 才送达，账面已有 2026H1 发布；"
                      "系统按当前策略与全部历史暴露重算，旧候选任务不能绕过新处置。"),
        },
        "disclaimer": "全部为虚构样例，不对应任何真实个人。",
    }
