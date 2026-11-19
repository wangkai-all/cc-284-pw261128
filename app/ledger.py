"""发布账本与候选任务持久化。

存储为单个 JSON 文件（data/state.json），写操作用线程锁 + 临时文件原子替换。
账本只追加、不物理删除：撤回仅置 status=withdrawn，已暴露行保留在知识库中。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from . import analysis, qi, seed, treatment
from .qi import QIError


class LedgerError(ValueError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Ledger:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                self.state = json.load(fh)
        else:
            self.state = seed.build_seed_state()
            self._flush()

    # ---------- 底层读写 ----------
    def _flush(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state, ensure_ascii=False))

    def _audit(self, action: str, **extra) -> None:
        entry = {"at": _now_iso(), "action": action}
        entry.update(extra)
        self.state["audit_log"].append(entry)

    # ---------- 查询 ----------
    def list_candidates(self) -> list[dict]:
        with self._lock:
            return [self._public_candidate(c) for c in self.state["candidates"]]

    def get_candidate(self, candidate_id: str) -> dict:
        with self._lock:
            for c in self.state["candidates"]:
                if c["candidate_id"] == candidate_id:
                    return json.loads(json.dumps(c, ensure_ascii=False))
        raise LedgerError(f"候选任务不存在: {candidate_id}")

    def _find_candidate(self, candidate_id: str) -> dict:
        for c in self.state["candidates"]:
            if c["candidate_id"] == candidate_id:
                return c
        raise LedgerError(f"候选任务不存在: {candidate_id}")

    def list_releases(self) -> list[dict]:
        with self._lock:
            out = []
            for r in self.state["releases"]:
                item = {k: v for k, v in r.items() if k not in ("artifact", "internal")}
                item["row_count"] = (
                    len(r["artifact"]["rows"]) if r.get("artifact") else 0
                )
                out.append(item)
            return out

    def knowledge(self, recipient_id: str) -> dict:
        with self._lock:
            entries = [r for r in self.state["releases"]
                       if r["recipient_id"] == recipient_id]
            return analysis.build_knowledge(entries)

    def policy(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state["policy"], ensure_ascii=False))

    def update_policy(self, small_threshold: int) -> dict:
        with self._lock:
            t = int(small_threshold)
            if t < 1 or t > 100:
                raise LedgerError("small_threshold 必须在 1..100 之间")
            self.state["policy"]["small_threshold"] = t
            self.state["policy"]["version"] += 1
            self.state["policy"]["updated_at"] = _now_iso()
            self.state["policy"]["history"].append({
                "version": self.state["policy"]["version"],
                "change": f"小群体审查线调整为 {t}（仅触发审查，不构成匿名保证）。",
                "at": self.state["policy"]["updated_at"],
            })
            self._audit("policy_update", small_threshold=t,
                        version=self.state["policy"]["version"])
            self._flush()
            return self.policy()

    # ---------- 分析（始终基于当前账本实时重算） ----------
    def analyze_candidate(self, candidate_id: str, plan: dict | None = None) -> dict:
        with self._lock:
            cand = self._find_candidate(candidate_id)
            kb = analysis.build_knowledge(
                [r for r in self.state["releases"]
                 if r["recipient_id"] == cand["recipient_id"]]
            )
            threshold = self.state["policy"]["small_threshold"]
            if plan is None:
                results = analysis.preset_analyses(cand, kb, threshold)
                return {
                    "candidate": self._public_candidate(cand),
                    "policy_version": self.state["policy"]["version"],
                    "late_arrival": results[0]["late_arrival"],
                    "presets": results,
                }
            return analysis.analyze(cand, treatment.validate_plan(plan), kb, threshold)

    # ---------- 发布 ----------
    def publish(self, candidate_id: str, plan: dict) -> dict:
        with self._lock:
            cand = self._find_candidate(candidate_id)
            if cand["status"] != "pending":
                raise LedgerError(
                    f"候选任务状态为 {cand['status']}，不可再次发布；"
                    "如需修订请基于其历史暴露创建新候选。"
                )
            plan = treatment.validate_plan(plan)

            # 关键：服务端按当前策略与全部历史暴露重算，前端传参不能决定风险结论
            kb = analysis.build_knowledge(
                [r for r in self.state["releases"]
                 if r["recipient_id"] == cand["recipient_id"]]
            )
            threshold = self.state["policy"]["small_threshold"]
            result = analysis.analyze(cand, plan, kb, threshold)
            applied = treatment.apply_plan(cand["rows"], plan)

            if plan["rejected"]:
                rel_id = self._next_release_id("R")
                entry = {
                    "release_id": rel_id,
                    "candidate_id": cand["candidate_id"],
                    "recipient_id": cand["recipient_id"],
                    "period": cand["period"],
                    "status": "rejected",
                    "created_at": _now_iso(),
                    "plan": plan,
                    "policy_version": self.state["policy"]["version"],
                    "artifact": None,
                    "internal": {"row_person_ids": []},
                    "withdrawn_at": None,
                    "withdraw_reason": None,
                    "note": "审查后整份拒发。",
                    "risk_snapshot": self._risk_snapshot(result),
                }
                self.state["releases"].append(entry)
                cand["status"] = "rejected"
                cand["resolved_by"] = rel_id
                self.state["policy"]["version"] += 1
                self._audit("reject", release_id=rel_id,
                            candidate_id=cand["candidate_id"])
                self._flush()
                return self._public_release(entry)

            artifact, person_ids = self._build_artifact(cand["rows"], plan)
            rel_id = self._next_release_id("P")
            entry = {
                "release_id": rel_id,
                "candidate_id": cand["candidate_id"],
                "recipient_id": cand["recipient_id"],
                "period": cand["period"],
                "status": "released",
                "created_at": _now_iso(),
                "plan": plan,
                "policy_version": self.state["policy"]["version"],
                "artifact": artifact,
                "internal": {"row_person_ids": person_ids},
                "withdrawn_at": None,
                "withdraw_reason": None,
                "note": "",
                "risk_snapshot": self._risk_snapshot(result),
            }
            self.state["releases"].append(entry)
            cand["status"] = "published"
            cand["resolved_by"] = rel_id
            self.state["policy"]["version"] += 1
            self._audit("publish", release_id=rel_id,
                        candidate_id=cand["candidate_id"], rows=len(person_ids))
            self._flush()
            return self._public_release(entry)

    def withdraw(self, release_id: str, reason: str) -> dict:
        with self._lock:
            entry = self._find_release(release_id)
            if entry["status"] != "released":
                raise LedgerError(f"发布 {release_id} 状态为 {entry['status']}，无法撤回")
            if not reason or not reason.strip():
                raise LedgerError("撤回必须填写原因")
            entry["status"] = "withdrawn"
            entry["withdrawn_at"] = _now_iso()
            entry["withdraw_reason"] = reason.strip()
            self.state["policy"]["version"] += 1
            self._audit("withdraw", release_id=release_id, reason=reason.strip())
            self._flush()
            return self._public_release(entry)

    def download_artifact(self, release_id: str) -> dict:
        with self._lock:
            entry = self._find_release(release_id)
            if entry["status"] != "released":
                raise LedgerError(
                    f"发布 {release_id} 已{ '撤回' if entry['status']=='withdrawn' else '拒发' }，"
                    "无可用下载（撤回不抹除已暴露事实，仅停止继续分发）。"
                )
            self._audit("download", release_id=release_id)
            self._flush()
            # HTTP 头必须是 latin-1 安全字符：主文件名用 ASCII，
            # 中文接收方名通过 RFC 5987 的 filename* 传递
            from urllib.parse import quote
            ascii_name = f"{release_id}_{entry['period']}.json"
            utf8_name = f"{release_id}_{entry['recipient_id']}_{entry['period']}.json"
            return {
                "filename": ascii_name,
                "filename_utf8": utf8_name,
                "filename_quoted": "UTF-8''" + quote(utf8_name),
                "content": {
                    "release_id": release_id,
                    "recipient_id": entry["recipient_id"],
                    "period": entry["period"],
                    "exported_at": _now_iso(),
                    "artifact": entry["artifact"],
                    "disclaimer": (
                        "本文件为去标识数据；去标识不等于匿名，接收方掌握的其他数据"
                        "可能与本文件组合导致重识别风险。"
                    ),
                },
            }

    # ---------- 新候选（上传虚构 JSON / 重发） ----------
    def add_candidate(self, payload: dict) -> dict:
        with self._lock:
            required = ["period", "recipient_id", "title", "rows"]
            missing = [k for k in required if not payload.get(k)]
            if missing:
                raise LedgerError(f"缺少字段: {', '.join(missing)}")
            rows = payload["rows"]
            if not isinstance(rows, list) or not rows:
                raise LedgerError("rows 必须是非空数组")
            cleaned = []
            for i, row in enumerate(rows):
                clean = {"person_id": str(row.get("person_id") or f"UPLOAD#{i+1}")}
                for f in ("age", "gender", "zip", "occupation",
                          "salary", "diagnosis"):
                    if f not in row:
                        raise LedgerError(f"第 {i+1} 行缺少字段 {f}")
                    clean[f] = row[f]
                # QI 值域立即校验：非法取值不允许进入账本
                try:
                    clean["age"] = int(clean["age"])
                    for f in qi.QI_FIELDS:
                        qi.generalize_value(f, clean[f], 0)
                except (QIError, ValueError, TypeError) as exc:
                    raise LedgerError(f"第 {i+1} 行准标识符非法: {exc}") from exc
                cleaned.append(clean)
            cand_id = payload.get("candidate_id") or self._next_candidate_id()
            if any(c["candidate_id"] == cand_id for c in self.state["candidates"]):
                raise LedgerError(f"候选 ID 已存在: {cand_id}")
            cand = {
                "candidate_id": cand_id,
                "period": str(payload["period"]),
                "recipient_id": str(payload["recipient_id"]),
                "title": str(payload["title"]),
                "source_file": str(payload.get("source_file") or "manual-upload.json"),
                "arrived_at": _now_iso(),
                "note": str(payload.get("note", "")),
                "status": "pending",
                "policy_version_at_arrival": self.state["policy"]["version"],
                "rows": cleaned,
            }
            self.state["candidates"].append(cand)
            self._audit("candidate_add", candidate_id=cand_id)
            self._flush()
            return self._public_candidate(cand)

    # ---------- 汇总 ----------
    def stats(self) -> dict:
        with self._lock:
            rels = self.state["releases"]
            cands = self.state["candidates"]
            downloadable = [r for r in rels if r["status"] == "released"]
            withdrawn = [r for r in rels if r["status"] == "withdrawn"]
            rejected = [r for r in rels if r["status"] == "rejected"]
            recipients = sorted({r["recipient_id"] for r in rels}
                                | {c["recipient_id"] for c in cands})
            per_recipient = {}
            for rid in recipients:
                kb = analysis.build_knowledge(
                    [r for r in rels if r["recipient_id"] == rid])
                per_recipient[rid] = {
                    "exposed_rows": len(kb["rows"]),
                    "exposed_periods": kb["periods"],
                    "release_count": sum(
                        1 for r in rels
                        if r["recipient_id"] == rid and r["status"] != "rejected"),
                    "withdrawn_count": sum(
                        1 for r in rels
                        if r["recipient_id"] == rid and r["status"] == "withdrawn"),
                    "rejected_count": sum(
                        1 for r in rels
                        if r["recipient_id"] == rid and r["status"] == "rejected"),
                }
            return {
                "candidate_total": len(cands),
                "candidate_pending": sum(1 for c in cands if c["status"] == "pending"),
                "released_total": len(downloadable),
                "withdrawn_total": len(withdrawn),
                "rejected_total": len(rejected),
                "exposed_rows_total": sum(v["exposed_rows"] for v in per_recipient.values()),
                "per_recipient": per_recipient,
                "policy_version": self.state["policy"]["version"],
                "audit_events": len(self.state["audit_log"]),
                "seed_summary": seed.seed_summary(),
            }

    def audit_log(self) -> list[dict]:
        with self._lock:
            return json.loads(json.dumps(self.state["audit_log"], ensure_ascii=False))

    # ---------- 内部辅助 ----------
    def _find_release(self, release_id: str) -> dict:
        for r in self.state["releases"]:
            if r["release_id"] == release_id:
                return r
        raise LedgerError(f"发布记录不存在: {release_id}")

    def _next_release_id(self, kind: str) -> str:
        prefix = "REL-RJ-" if kind == "R" else "REL-"
        n = sum(1 for r in self.state["releases"] if r["release_id"].startswith(prefix))
        # 用时间戳后缀避免与种子编号冲突
        return f"{prefix}{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{n+1:02d}"

    def _next_candidate_id(self) -> str:
        return f"CAND-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    @staticmethod
    def _build_artifact(rows: list[dict], plan: dict) -> tuple[dict, list[str]]:
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

    @staticmethod
    def _risk_snapshot(result: dict) -> dict:
        s = result["summary"]
        return {
            "singleton_classes": s["singleton_classes"],
            "small_classes": s["small_classes"],
            "shrunk_classes": s["shrunk_classes"],
            "flagged_person_ids": s["flagged_person_ids"],
            "late_arrival": s["late_arrival"],
            "loss": result["loss"],
        }

    @staticmethod
    def _public_candidate(c: dict) -> dict:
        return json.loads(json.dumps(c, ensure_ascii=False))

    @staticmethod
    def _public_release(r: dict) -> dict:
        item = {k: v for k, v in r.items() if k not in ("artifact", "internal")}
        item["row_count"] = len(r["artifact"]["rows"]) if r.get("artifact") else 0
        return item
