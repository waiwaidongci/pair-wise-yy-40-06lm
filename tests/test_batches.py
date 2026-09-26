import tempfile, unittest
from datetime import date, timedelta
from pathlib import Path

from src.domain import ConflictError, PermissionDenied, ValidationError
from src.repository import Repository
from src.service import Service
from src.rules import STATES, TRANSITION_ROLES


def advance_to_terminal(service, item):
    current = item
    for target in ("assessed", "design", "construction", "accepted"):
        current = service.transition(
            current["id"], target, current["version"], "reviewer",
            TRANSITION_ROLES[target][0])
    return current


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(str(Path(self.tmp.name) / "test.db"))
        self.service = Service(self.repo)
        self.item_a = self.service.create_item(
            {"title": "一号楼", "description": "d", "severity": "high",
             "quantity": 12, "threshold": 6, "external_ref": "B-A"},
            "creator", "assessor")
        self.item_b = self.service.create_item(
            {"title": "二号楼", "description": "d", "severity": "low",
             "quantity": 1, "threshold": 10, "external_ref": "B-B"},
            "creator", "assessor")
        self.item_c = self.service.create_item(
            {"title": "三号楼", "description": "d", "severity": "severe",
             "quantity": 30, "threshold": 10, "external_ref": "B-C"},
            "creator", "assessor")
        self.future = (date.today() + timedelta(days=5)).isoformat()

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def make_batch(self, ids=None, planned=None):
        return self.service.create_batch(
            {"scope": "城北片区", "planned_date": planned or self.future,
             "item_ids": ids or [self.item_a["id"], self.item_b["id"]]},
            "assessor1", "assessor")

    def test_create_and_priority(self):
        batch = self.make_batch()
        self.assertFalse(batch["reused"])
        self.assertEqual(batch["item_count"], 2)
        self.assertIn("priority", batch)
        self.assertGreater(batch["priority"], 0)
        # 更紧急的批次（severe + 临期）优先级更高
        urgent = self.service.create_batch(
            {"scope": "学校", "planned_date": date.today().isoformat(),
             "item_ids": [self.item_c["id"]]},
            "assessor1", "assessor")
        self.assertGreater(urgent["priority"], batch["priority"])

    def test_duplicate_in_active_batch_returns_existing(self):
        first = self.make_batch()
        # 同一项目再建批，返回原批次
        again = self.service.create_batch(
            {"scope": "另一个范围", "planned_date": self.future,
             "item_ids": [self.item_a["id"]]},
            "assessor2", "assessor")
        self.assertTrue(again["reused"])
        self.assertEqual(again["id"], first["id"])
        # 不同项目组合命中其中一个活动批次也返回原批次
        mix = self.service.create_batch(
            {"scope": "混合", "planned_date": self.future,
             "item_ids": [self.item_b["id"], self.item_c["id"]]},
            "assessor2", "assessor")
        self.assertEqual(mix["id"], first["id"])
        self.assertEqual(len(self.repo.list_batches()), 1)

    def test_validation(self):
        with self.assertRaises(ValidationError):
            self.service.create_batch(
                {"scope": "  ", "planned_date": self.future,
                 "item_ids": [self.item_a["id"]]}, "a", "assessor")
        with self.assertRaises(ValidationError):
            self.service.create_batch(
                {"scope": "s", "planned_date": "2026/01/01",
                 "item_ids": [self.item_a["id"]]}, "a", "assessor")
        with self.assertRaises(ValidationError):
            self.service.create_batch(
                {"scope": "s", "planned_date": self.future, "item_ids": []},
                "a", "assessor")

    def test_close_blocked_then_unblocked(self):
        batch = self.make_batch()
        # 项目未到终态 -> 阻塞
        detail = self.service.get_batch(batch["id"], "chief_engineer")
        self.assertFalse(detail["closeable"])
        self.assertEqual(len(detail["blockers"]), 2)
        blocker = detail["blockers"][0]
        self.assertTrue(any("未到终态" in r for r in blocker["reasons"]))
        with self.assertRaises(ConflictError):
            self.service.close_batch(batch["id"], "chief", "chief_engineer")
        # 非总工不能关闭
        with self.assertRaises(PermissionDenied):
            self.service.close_batch(batch["id"], "chief", "assessor")
        # 关掉记录并推进到终态
        for item in (self.item_a, self.item_b):
            cur = self.repo.get_item(item["id"])
            cur = self.repo.transition_item(cur["id"], "assessed", cur["version"], "x")
            cur = self.repo.transition_item(cur["id"], "design", cur["version"], "x")
            cur = self.repo.transition_item(cur["id"], "construction", cur["version"], "x")
            self.repo.transition_item(cur["id"], "accepted", cur["version"], "x")
        detail = self.service.get_batch(batch["id"], "chief_engineer")
        self.assertTrue(detail["closeable"])
        closed = self.service.close_batch(batch["id"], "chief", "chief_engineer")
        self.assertEqual(closed["status"], "closed")
        # 重复关闭冲突
        with self.assertRaises(ConflictError):
            self.service.close_batch(batch["id"], "chief", "chief_engineer")

    def test_open_record_blocks_close(self):
        batch = self.make_batch()
        self.service.add_record(
            self.item_a["id"],
            {"kind": "defect", "detail": "裂缝待复检", "status": "open"},
            "a", "assessor")
        for item in (self.item_a, self.item_b):
            cur = self.repo.get_item(item["id"])
            cur = self.repo.transition_item(cur["id"], "assessed", cur["version"], "x")
            cur = self.repo.transition_item(cur["id"], "design", cur["version"], "x")
            cur = self.repo.transition_item(cur["id"], "construction", cur["version"], "x")
            self.repo.transition_item(cur["id"], "accepted", cur["version"], "x")
        detail = self.service.get_batch(batch["id"], "chief_engineer")
        self.assertEqual(len(detail["blockers"]), 1)
        self.assertIn("未关闭事项", "".join(detail["blockers"][0]["reasons"]))

    def test_list_filters_and_reuse_after_close(self):
        # 批次1：项目已到终态、无未关闭事项 -> 待关闭
        ready = self.make_batch([self.item_a["id"]])
        cur = self.repo.get_item(self.item_a["id"])
        cur = self.repo.transition_item(cur["id"], "assessed", cur["version"], "x")
        cur = self.repo.transition_item(cur["id"], "design", cur["version"], "x")
        cur = self.repo.transition_item(cur["id"], "construction", cur["version"], "x")
        self.repo.transition_item(cur["id"], "accepted", cur["version"], "x")
        # 批次2：项目未完成 -> 进行中
        blocked = self.make_batch([self.item_c["id"]])

        in_progress = self.service.list_batches("viewer", "in_progress")
        self.assertEqual([b["id"] for b in in_progress], [blocked["id"]])
        closeable = self.service.list_batches("viewer", "closeable")
        self.assertEqual([b["id"] for b in closeable], [ready["id"]])
        active = self.service.list_batches("viewer", "active")
        self.assertEqual(len(active), 2)

        self.service.close_batch(ready["id"], "chief", "chief_engineer")
        closed = self.service.list_batches("viewer", "closed")
        self.assertEqual([b["id"] for b in closed], [ready["id"]])
        # 已关闭批次释放项目，可重新建批
        new_batch = self.service.create_batch(
            {"scope": "复查", "planned_date": self.future,
             "item_ids": [self.item_a["id"]]},
            "assessor1", "assessor")
        self.assertFalse(new_batch["reused"])
        self.assertNotEqual(new_batch["id"], ready["id"])
        self.assertTrue(self.repo.verify_audit_chain())


if __name__ == "__main__":
    unittest.main()
