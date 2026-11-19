import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

from app import seed, server, treatment
from app.ledger import Ledger, LedgerError


def _post(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _patch(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="PATCH")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _get(url):
    with urllib.request.urlopen(url) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _get_error_expected(url):
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "state.json")
        self.ledger = Ledger(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_loaded_and_persisted(self):
        self.assertTrue(os.path.exists(self.path))
        stats = self.ledger.stats()
        self.assertGreaterEqual(stats["candidate_pending"], 4)
        # 重新打开后状态一致
        again = Ledger(self.path)
        self.assertEqual(again.stats()["candidate_total"], stats["candidate_total"])

    def test_publish_flow_and_ledger_sync(self):
        before = self.ledger.stats()
        entry = self.ledger.publish("CAND-2026H2-A", treatment.plan_bucket())
        self.assertEqual(entry["status"], "released")
        self.assertEqual(entry["row_count"], 9)  # 无抑制，9 行全发布
        # 候选状态同步
        cand = self.ledger.get_candidate("CAND-2026H2-A")
        self.assertEqual(cand["status"], "published")
        self.assertEqual(cand["resolved_by"], entry["release_id"])
        # 汇总同步：可下载数 +1，待审查 -1
        after = self.ledger.stats()
        self.assertEqual(after["released_total"], before["released_total"] + 1)
        self.assertEqual(after["candidate_pending"], before["candidate_pending"] - 1)
        # 下载内容不含 person_id
        pkg = self.ledger.download_artifact(entry["release_id"])
        raw = json.dumps(pkg["content"], ensure_ascii=False)
        self.assertNotIn("person_id", raw)
        self.assertNotIn("P041", raw)

    def test_double_publish_blocked(self):
        self.ledger.publish("CAND-2026H2-A", treatment.plan_bucket())
        with self.assertRaises(LedgerError):
            self.ledger.publish("CAND-2026H2-A", treatment.plan_passthrough())

    def test_reject_creates_rejected_entry_no_artifact(self):
        entry = self.ledger.publish("CAND-2026H2-B", treatment.plan_reject())
        self.assertEqual(entry["status"], "rejected")
        self.assertEqual(entry["row_count"], 0)
        with self.assertRaises(LedgerError):
            self.ledger.download_artifact(entry["release_id"])

    def test_withdraw_keeps_exposure_but_blocks_download(self):
        entry = self.ledger.publish("CAND-2026H2-A", treatment.plan_bucket())
        rid = entry["release_id"]
        self.ledger.withdraw(rid, "发布后发现外部数据集可组合")
        with self.assertRaises(LedgerError):
            self.ledger.download_artifact(rid)
        # 知识库仍包含撤回的行：累计暴露行数不减少
        stats = self.ledger.stats()
        recipient = stats["per_recipient"][seed.RECIPIENT_A]
        exposed_before_withdraw = recipient["exposed_rows"]
        self.assertGreaterEqual(exposed_before_withdraw, 9)
        self.assertEqual(recipient["withdrawn_count"], 1)
        # 再次撤回被拒绝
        with self.assertRaises(LedgerError):
            self.ledger.withdraw(rid, "重复撤回")
        # 撤回必须有原因
        with self.assertRaises(LedgerError):
            fresh = self.ledger.publish("CAND-2026H2-B", treatment.plan_bucket())
            self.ledger.withdraw(fresh["release_id"], "  ")

    def test_republish_accounts_for_prior_history(self):
        # 先发布 2026H2（含 P041），再为同一接收方新建后续候选；分析必须看到新发布
        entry = self.ledger.publish("CAND-2026H2-A", treatment.plan_passthrough())
        self.assertEqual(entry["status"], "released")
        kb = self.ledger.knowledge(seed.RECIPIENT_A)
        release_ids = {m["release_id"] for m in kb["release_meta"].values()}
        self.assertIn(entry["release_id"], release_ids)
        # 新上传候选走完整分析，历史发布全部参与
        cand = self.ledger.add_candidate({
            "period": "2027H1", "recipient_id": seed.RECIPIENT_A,
            "title": "后续虚构候选",
            "rows": [{
                "person_id": "P041", "age": 37, "gender": "男", "zip": "10009",
                "occupation": "软件工程师", "salary": 12000, "diagnosis": "高血压",
            }],
        })
        bundle = self.ledger.analyze_candidate(cand["candidate_id"])
        baseline = bundle["presets"][0]
        p041 = next(cl for cl in baseline["classes"])
        # 与上一份原样发布（同粒度单人）以及更早的分桶发布都相容
        self.assertGreaterEqual(len(p041["prior_matches"]), 2)

    def test_late_candidate_uses_current_policy_and_full_ledger(self):
        bundle = self.ledger.analyze_candidate("CAND-2025H2-A-LATE")
        self.assertTrue(bundle["late_arrival"])
        # 送达时策略 v3，分析用的也是当前 v3
        self.assertEqual(bundle["policy_version"], 3)
        # 分析中能看到 2026H1 的发布（晚于该候选数据期）
        baseline = bundle["presets"][0]
        seen_periods = {m["period"] for cl in baseline["classes"]
                        for m in cl["prior_matches"]}
        self.assertIn("2026H1", seen_periods)

    def test_policy_update_bumps_version_and_keeps_history(self):
        p0 = self.ledger.policy()
        p1 = self.ledger.update_policy(3)
        self.assertEqual(p1["version"], p0["version"] + 1)
        self.assertEqual(p1["small_threshold"], 3)
        self.assertEqual(len(p1["history"]), len(p0["history"]) + 1)
        # 新阈值即时生效于分析
        bundle = self.ledger.analyze_candidate("CAND-2026H2-A")
        self.assertEqual(bundle["presets"][0]["small_threshold"], 3)
        with self.assertRaises(LedgerError):
            self.ledger.update_policy(0)

    def test_add_candidate_validation(self):
        with self.assertRaises(LedgerError):
            self.ledger.add_candidate({"period": "2027H1"})
        with self.assertRaises(LedgerError):
            self.ledger.add_candidate({
                "period": "2027H1", "recipient_id": "X", "title": "t", "rows": []})
        created = self.ledger.add_candidate({
            "period": "2027H1", "recipient_id": "X", "title": "t",
            "rows": [{"age": 30, "gender": "男", "zip": "10009",
                      "occupation": "厨师", "salary": 5000, "diagnosis": "体检无异常"}],
        })
        self.assertTrue(created["candidate_id"].startswith("CAND-"))
        self.assertEqual(created["rows"][0]["person_id"], "UPLOAD#1")

    def test_add_candidate_rejects_invalid_qi(self):
        good = {"age": 30, "gender": "男", "zip": "10009",
                "occupation": "厨师", "salary": 5000, "diagnosis": "体检无异常"}
        bad_zip = dict(good, zip="99999")
        with self.assertRaises(LedgerError):
            self.ledger.add_candidate({
                "period": "2027H1", "recipient_id": "X", "title": "t",
                "rows": [bad_zip]})
        bad_occ = dict(good, occupation="宇航员")
        with self.assertRaises(LedgerError):
            self.ledger.add_candidate({
                "period": "2027H1", "recipient_id": "X", "title": "t",
                "rows": [bad_occ]})
        # 非法候选不落账
        self.assertEqual(
            self.ledger.stats()["candidate_total"],
            Ledger(self.path).stats()["candidate_total"])


class TestHTTPApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "http-state.json")
        server.reset_ledger_for_tests()
        server.get_ledger(cls.path)  # 初始化独立账本
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.tmp.cleanup()

    def test_01_pages_and_stats(self):
        status, stats = _get(f"{self.base}/api/stats")
        self.assertEqual(status, 200)
        self.assertIn("per_recipient", stats)
        with urllib.request.urlopen(f"{self.base}/") as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers["Content-Type"])
        with urllib.request.urlopen(f"{self.base}/app.js") as resp:
            self.assertEqual(resp.status, 200)

    def test_02_analysis_and_publish_and_withdraw(self):
        cid = "CAND-2026H2-A"
        status, bundle = _get(f"{self.base}/api/analysis/{cid}")
        self.assertEqual(status, 200)
        self.assertEqual(len(bundle["presets"]), 4)
        # 自定义方案分析
        # 分桶不消除本期人数缩小（P001 未参与本期，同粒度桶仍 2→1）
        status, res = _post(f"{self.base}/api/analysis/{cid}",
                            {"plan": treatment.plan_bucket()})
        self.assertEqual(status, 200)
        self.assertEqual(res["summary"]["shrunk_classes"], 1)
        # 发布
        status, rel = _post(f"{self.base}/api/publish/{cid}",
                            {"plan": treatment.plan_bucket()})
        self.assertEqual(status, 201)
        rid = rel["release_id"]
        # 重复发布被拒
        status, err = _post(f"{self.base}/api/publish/{cid}",
                            {"plan": treatment.plan_bucket()})
        self.assertEqual(status, 400)
        self.assertIn("不可再次发布", err["message"])
        # 下载
        status, pkg = _get(f"{self.base}/api/download/{rid}")
        self.assertEqual(status, 200)
        self.assertNotIn("person_id", json.dumps(pkg, ensure_ascii=False))
        # 撤回
        status, w = _patch(f"{self.base}/api/withdraw/{rid}",
                           {"reason": "API 端到端测试撤回"})
        self.assertEqual(status, 200)
        self.assertEqual(w["status"], "withdrawn")
        # 撤回后下载 400
        status, err = _get_error_expected(f"{self.base}/api/download/{rid}")
        self.assertEqual(status, 400)

    def test_03_bad_plan_returns_400(self):
        status, err = _post(f"{self.base}/api/analysis/CAND-2026H2-B",
                            {"plan": {"levels": {"age": 99}}})
        self.assertEqual(status, 400)
        self.assertEqual(err["error"], "PlanError")

    def test_04_late_candidate_analyzed_against_full_history(self):
        status, bundle = _get(f"{self.base}/api/analysis/CAND-2025H2-A-LATE")
        self.assertEqual(status, 200)
        self.assertTrue(bundle["late_arrival"])


if __name__ == "__main__":
    unittest.main()
