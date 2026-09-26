import tempfile, unittest
from pathlib import Path
from src import rules
from src.domain import ConflictError, NotFoundError, PermissionDenied, ValidationError
from src.repository import Repository
from src.service import Service
from src.rules import STATES, TRANSITION_ROLES
class BatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.repo=Repository(str(Path(self.tmp.name)/"test.db")); self.service=Service(self.repo)
        self.items=[self.service.create_item({"title":f"楼-{n}","description":"老楼抗震复查","severity":s,"quantity":12,"threshold":6,"external_ref":f"B-{n}"},"creator","assessor") for n,s in enumerate(['high','medium','low'],1)]
    def tearDown(self): self.repo.close(); self.tmp.cleanup()
    def _ids(self): return [i["id"] for i in self.items]
    def _create(self,**kw):
        payload={"scope":kw.pop("scope","城东片区老楼复查"),"planned_review_date":kw.pop("planned_review_date","2026-10-15"),"item_ids":kw.pop("item_ids",self._ids())}
        payload.update(kw); return self.service.create_batch(payload,"planner","assessor")
    def _finish(self,item):
        self.service.add_record(item["id"],{"kind":"evidence","detail":"done","status":"closed"},"recorder","assessor")
        current=item
        for target in STATES[1:5]: current=self.service.transition(current["id"],target,current["version"],"worker",TRANSITION_ROLES[target][0])
        return current
    def test_create_and_deduplicate(self):
        batch=self._create(); self.assertTrue(batch["created"]); self.assertEqual(batch["status"],"open"); self.assertEqual(batch["item_count"],3)
        again=self._create(scope="换个范围也无效")
        self.assertFalse(again["created"]); self.assertEqual(again["id"],batch["id"])
        partial=self._create(item_ids=[self._ids()[0]])
        self.assertFalse(partial["created"]); self.assertEqual(partial["id"],batch["id"])
        self.assertEqual(len(self.service.list_batches("viewer")),1)
    def test_create_validation_and_permission(self):
        with self.assertRaises(PermissionDenied): self.service.create_batch({"scope":"x","planned_review_date":"2026-10-15","item_ids":self._ids()},"planner","viewer")
        with self.assertRaises(ValidationError): self._create(item_ids=[])
        with self.assertRaises(ValidationError): self._create(item_ids=["a"])
        with self.assertRaises(ValidationError): self._create(planned_review_date="2026/10/15")
        with self.assertRaises(NotFoundError): self._create(item_ids=[9999])
    def test_close_blocked_and_detail_lists_blockers(self):
        batch=self._create()
        self.service.add_record(self.items[0]["id"],{"kind":"issue","detail":"裂缝待复核","status":"open"},"recorder","assessor")
        with self.assertRaises(ConflictError) as ctx: self.service.close_batch(batch["id"],batch["version"],"chief","structural_engineer")
        message=str(ctx.exception); self.assertIn("阻塞项目",message); self.assertIn("未到终态",message); self.assertIn("仍有未关闭事项",message)
        detail=self.service.get_batch(batch["id"],"viewer")
        self.assertEqual(len(detail["blockers"]),3)
        reasons={b["item_id"]:b["reasons"] for b in detail["blockers"]}
        self.assertEqual(reasons[self.items[0]["id"]],["未到终态","仍有未关闭事项"])
        with self.assertRaises(PermissionDenied): self.service.close_batch(batch["id"],batch["version"],"planner","assessor")
    def test_close_requires_terminal_items_and_closed_records(self):
        batch=self._create()
        for item in self.items: self._finish(item)
        detail=self.service.get_batch(batch["id"],"viewer"); self.assertEqual(detail["blockers"],[]); self.assertEqual(detail["list_state"],"closable")
        closed=self.service.close_batch(batch["id"],batch["version"],"chief","structural_engineer")
        self.assertEqual(closed["status"],"closed"); self.assertEqual(closed["list_state"],"closed"); self.assertIsNotNone(closed["closed_at"])
        with self.assertRaises(ConflictError): self.service.close_batch(batch["id"],closed["version"],"chief","structural_engineer")
        rebatch=self._create(); self.assertTrue(rebatch["created"]); self.assertNotEqual(rebatch["id"],batch["id"])
    def test_list_filters(self):
        active=self._create()
        self.assertEqual([b["id"] for b in self.service.list_batches("viewer","active")],[active["id"]])
        self.assertEqual(self.service.list_batches("viewer","closable"),[])
        for item in self.items: self._finish(item)
        self.assertEqual([b["id"] for b in self.service.list_batches("viewer","closable")],[active["id"]])
        self.service.close_batch(active["id"],active["version"],"chief","structural_engineer")
        self.assertEqual([b["id"] for b in self.service.list_batches("viewer","closed")],[active["id"]])
        self.assertEqual(self.service.list_batches("viewer","active"),[])
        with self.assertRaises(ValidationError): self.service.list_batches("viewer","bogus")
    def test_priority_recalculation(self):
        urgent=rules.batch_priority([9],open_records=4,remaining_hours=[-1.0]); calm=rules.batch_priority([2],open_records=0,remaining_hours=[200.0])
        self.assertGreater(urgent,calm); self.assertEqual(rules.batch_priority([]),0)
        self.assertGreater(rules.batch_priority([5],open_records=4,remaining_hours=[100.0]),rules.batch_priority([5],open_records=0,remaining_hours=[100.0]))
        self.assertGreater(rules.batch_priority([5],remaining_hours=[10.0]),rules.batch_priority([5],remaining_hours=[100.0]))
        batch=self._create(); self.assertGreaterEqual(batch["priority"],max(i["priority"] for i in batch["items"]))
    def test_audit_trail(self):
        batch=self._create()
        for item in self.items: self._finish(item)
        self.service.close_batch(batch["id"],batch["version"],"chief","structural_engineer")
        actions=[e["action"] for e in self.repo.list_audit()]
        self.assertIn("batch_create",actions); self.assertIn("batch_close",actions); self.assertTrue(self.repo.verify_audit_chain())
if __name__=="__main__": unittest.main()
