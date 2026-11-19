import unittest

from app import treatment
from app.treatment import PlanError


def _rows():
    return [
        {"person_id": "A", "age": 34, "gender": "男", "zip": "10009",
         "occupation": "软件工程师", "salary": 10000, "diagnosis": "高血压"},
        {"person_id": "B", "age": 41, "gender": "女", "zip": "20005",
         "occupation": "医生", "salary": 8000, "diagnosis": "偏头痛"},
        {"person_id": "C", "age": 57, "gender": "女", "zip": "61017",
         "occupation": "药剂师", "salary": 6000, "diagnosis": "高血压"},
    ]


class TestTreatment(unittest.TestCase):
    def test_validate_wraps_qi_error(self):
        with self.assertRaises(PlanError):
            treatment.validate_plan({"levels": {"age": 9}})
        with self.assertRaises(PlanError):
            treatment.validate_plan({"suppressed_person_ids": "A"})

    def test_bucketing_plan_and_apply(self):
        plan = treatment.plan_bucket()
        applied = treatment.apply_plan(_rows(), plan)
        self.assertEqual(len(applied["released"]), 3)
        first = applied["released"][0]
        self.assertEqual(first["qi_display"]["age"], "30-39")
        self.assertEqual(first["qi_display"]["zip"], "10***")
        self.assertEqual(first["qi_display"]["occupation"], "工程技术类")
        # 原始值保留在 qi_raw 中用于差异展示
        self.assertEqual(first["qi_raw"]["occupation"], "软件工程师")

    def test_suppression(self):
        plan = treatment.plan_suppress(["A", "C"])
        applied = treatment.apply_plan(_rows(), plan)
        self.assertEqual([r["person_id"] for r in applied["suppressed"]], ["A", "C"])
        self.assertEqual([r["person_id"] for r in applied["released"]], ["B"])
        diffs = {d["person_id"]: d for d in applied["row_diffs"]}
        self.assertEqual(diffs["A"]["action"], "suppressed")
        self.assertIsNone(diffs["A"]["after"]["age"])

    def test_rejection(self):
        applied = treatment.apply_plan(_rows(), treatment.plan_reject())
        self.assertEqual(applied["released"], [])
        self.assertEqual(len(applied["suppressed"]), 3)
        self.assertTrue(all(d["action"] == "rejected" for d in applied["row_diffs"]))

    def test_loss_metrics_monotonic_and_complete(self):
        rows = _rows()
        base = treatment.apply_plan(rows, treatment.plan_passthrough())
        loss_base = treatment.measure_loss(rows, base, treatment.plan_passthrough())
        self.assertEqual(loss_base["row_suppression_rate"], 0.0)
        self.assertEqual(loss_base["generalization_loss"], 0.0)
        self.assertEqual(loss_base["utility_score"], 1.0)

        bucket = treatment.apply_plan(rows, treatment.plan_bucket())
        loss_bucket = treatment.measure_loss(rows, bucket, treatment.plan_bucket())
        self.assertGreater(loss_bucket["generalization_loss"], 0.0)
        self.assertLess(loss_bucket["utility_score"], 1.0)

        rej = treatment.apply_plan(rows, treatment.plan_reject())
        loss_rej = treatment.measure_loss(rows, rej, treatment.plan_reject())
        self.assertEqual(loss_rej["utility_score"], 0.0)
        self.assertEqual(loss_rej["released_count"], 0)

    def test_artifact_never_contains_person_id(self):
        applied = treatment.apply_plan(_rows(), treatment.plan_bucket())
        for r in applied["released"]:
            out = {"qi_display": r["qi_display"],
                   "salary": r["salary"], "diagnosis": r["diagnosis"]}
            self.assertNotIn("person_id", out)


if __name__ == "__main__":
    unittest.main()
