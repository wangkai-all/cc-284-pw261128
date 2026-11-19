import unittest

from app import analysis, seed, treatment


class TestAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = seed.build_seed_state()
        cls.a_releases = [r for r in cls.state["releases"]
                          if r["recipient_id"] == seed.RECIPIENT_A]
        cls.kb = analysis.build_knowledge(cls.a_releases)

    def test_knowledge_includes_all_nonrejected(self):
        # 拒发条目不贡献行；元信息保留以便审计追溯
        self.assertEqual(
            sum(1 for m in self.kb["release_meta"].values() if m["has_artifact"]), 3)
        rejected = self.kb["release_meta"]["REL-2025-R01"]
        self.assertFalse(rejected["has_artifact"])
        self.assertNotIn("REL-2025-R01", {r["release_id"] for r in self.kb["rows"]})
        self.assertIn("REL-2025-001", {m["release_id"] for m in self.kb["release_meta"].values()})
        self.assertIn("REL-2025-002", {m["release_id"] for m in self.kb["release_meta"].values()})
        self.assertIn("REL-2026-001", {m["release_id"] for m in self.kb["release_meta"].values()})
        self.assertEqual(self.kb["periods"], ["2025H1", "2025H2", "2026H1"])

    def test_p041_shrunk_chain(self):
        cand = next(c for c in self.state["candidates"]
                    if c["candidate_id"] == "CAND-2026H2-A")
        res = analysis.analyze(cand, treatment.plan_passthrough(), self.kb, 2)
        p041 = next(cl for cl in res["classes"] if "P041" in cl["candidate_person_ids"])
        self.assertEqual(p041["candidate_size"], 1)
        self.assertIn("shrunk", p041["flags"])
        shrink = next(d for d in p041["shrink_details"]
                      if d["release_id"] == "REL-2025-002")
        self.assertEqual((shrink["prior_size"], shrink["candidate_size"]), (2, 1))
        self.assertIn("singleton", p041["flags"])
        self.assertIn("small", p041["flags"])
        self.assertEqual(res["summary"]["shrunk_classes"], 1)

    def test_late_arrival_flag(self):
        late = next(c for c in self.state["candidates"]
                    if c["candidate_id"] == "CAND-2025H2-A-LATE")
        res = analysis.analyze(late, treatment.plan_passthrough(), self.kb, 2)
        self.assertTrue(res["late_arrival"])

    def test_new_recipient_empty_knowledge(self):
        cand = next(c for c in self.state["candidates"]
                    if c["candidate_id"] == "CAND-2026H1-B")
        kb_b = analysis.build_knowledge(
            [r for r in self.state["releases"] if r["recipient_id"] == seed.RECIPIENT_B])
        self.assertEqual(kb_b["rows"], [])
        res = analysis.analyze(cand, treatment.plan_passthrough(), kb_b, 2)
        self.assertFalse(res["late_arrival"])
        self.assertTrue(all(not cl["shrunk"] for cl in res["classes"]))

    def test_bucketing_does_not_eliminate_shrink_when_members_leave(self):
        # 分桶把组合提升到与历史同粒度，但新文件该桶内只有 P041（P001 未参与本期），
        # 群体仍从 2 缩到 1：shrunk 检测与粒度无关，只看相容桶的人数变化。
        cand = next(c for c in self.state["candidates"]
                    if c["candidate_id"] == "CAND-2026H2-A")
        res = analysis.analyze(cand, treatment.plan_bucket(), self.kb, 2)
        self.assertEqual(res["summary"]["shrunk_classes"], 1)
        shrink = next(d for cl in res["classes"] for d in cl["shrink_details"]
                      if d["release_id"] == "REL-2025-002")
        self.assertEqual((shrink["prior_size"], shrink["candidate_size"]), (2, 1))

    def test_higher_bucketing_plus_suppression_clears_flagged(self):
        # 更激进分桶（职业也打码到 *）并抑制被标记行后，发布集合不再含任何信号类
        cand = next(c for c in self.state["candidates"]
                    if c["candidate_id"] == "CAND-2026H2-A")
        baseline = analysis.analyze(cand, treatment.plan_passthrough(), self.kb, 2)
        plan = treatment.plan_bucket(
            {"age": 3, "gender": 1, "zip": 3, "occupation": 2},
            extra_suppressed=baseline["summary"]["flagged_person_ids"])
        res = analysis.analyze(cand, plan, self.kb, 2)
        self.assertEqual(res["summary"]["shrunk_classes"], 0)
        self.assertEqual(res["summary"]["singleton_classes"], 0)

    def test_reject_plan_releases_nothing(self):
        cand = next(c for c in self.state["candidates"]
                    if c["candidate_id"] == "CAND-2026H2-A")
        res = analysis.analyze(cand, treatment.plan_reject(), self.kb, 2)
        self.assertEqual(res["applied_counts"]["released"], 0)
        self.assertEqual(res["loss"]["overall_loss"], 1.0)
        self.assertEqual(res["classes"], [])

    def test_suppression_targets_flagged(self):
        cand = next(c for c in self.state["candidates"]
                    if c["candidate_id"] == "CAND-2026H2-A")
        baseline = analysis.analyze(cand, treatment.plan_passthrough(), self.kb, 2)
        flagged = baseline["summary"]["flagged_person_ids"]
        self.assertIn("P041", flagged)
        res = analysis.analyze(
            cand, treatment.plan_suppress(flagged), self.kb, 2)
        released_ids = {pid for cl in res["classes"] for pid in cl["candidate_person_ids"]}
        self.assertTrue(released_ids.isdisjoint(flagged))
        self.assertEqual(res["summary"]["suppressed_rows"], len(flagged))

    def test_withdrawn_release_still_in_knowledge(self):
        # 模拟撤回：状态改变但行仍在知识库
        releases = [dict(r) for r in self.a_releases]
        for r in releases:
            if r["release_id"] == "REL-2025-002":
                r["status"] = "withdrawn"
        kb = analysis.build_knowledge(releases)
        meta = kb["release_meta"]["REL-2025-002"]
        self.assertEqual(meta["status"], "withdrawn")
        cand = next(c for c in self.state["candidates"]
                    if c["candidate_id"] == "CAND-2026H2-A")
        res = analysis.analyze(cand, treatment.plan_passthrough(), kb, 2)
        p041 = next(cl for cl in res["classes"] if "P041" in cl["candidate_person_ids"])
        match = next(m for m in p041["prior_matches"]
                     if m["release_id"] == "REL-2025-002")
        self.assertEqual(match["status"], "withdrawn")
        self.assertTrue(p041["shrunk"])

    def test_preset_analyses_order_and_counts(self):
        cand = next(c for c in self.state["candidates"]
                    if c["candidate_id"] == "CAND-2026H2-A")
        presets = analysis.preset_analyses(cand, self.kb, 2)
        self.assertEqual([p["preset_code"] for p in presets],
                         ["passthrough", "suppress", "bucket", "reject"])
        # 拒发损失最大，原样发布抑制为零
        self.assertEqual(presets[0]["loss"]["suppressed_count"], 0)
        self.assertEqual(presets[3]["loss"]["overall_loss"], 1.0)
        # 综合损失排序合理：拒发 >= 其他
        for p in presets[:3]:
            self.assertLessEqual(p["loss"]["overall_loss"], 1.0)


if __name__ == "__main__":
    unittest.main()
