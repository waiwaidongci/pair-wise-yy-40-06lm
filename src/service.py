from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from .domain import (ConflictError, ensure_role, normalize_severity,
                     require_date, require_id_list, require_number, require_text)
from .repository import Repository
from .rules import (ACTIVE_BATCH, AUDIT_ROLES, BATCH_CREATE_ROLES,
                    BATCH_CLOSE_ROLES, BATCH_ENTITY, BATCH_VIEW_ROLES,
                    CREATE_ROLES, ENTITY, RECORD_ROLES, TERMINAL_STATES,
                    TITLE, VIEW_ROLES, batch_blockers, batch_priority,
                    completion_blockers, days_remaining, escalation_required,
                    priority_score, response_deadline_hours, role_for_transition,
                    validate_transition)


class Service:
    def __init__(self, repository: Repository):
        self.repository = repository

    def _view(self, role: str) -> None:
        ensure_role(role, VIEW_ROLES)

    def create_item(self, payload: Dict[str, Any], actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, CREATE_ROLES)
        actor = require_text(actor, "actor", 100)
        title = require_text(payload.get("title"), "title", 200)
        description = require_text(payload.get("description"), "description")
        severity = normalize_severity(payload.get("severity"))
        quantity = require_number(payload.get("quantity", 0), "quantity")
        threshold = require_number(payload.get("threshold", 1), "threshold", 0.000001)
        external_ref = payload.get("external_ref")
        if external_ref is not None:
            external_ref = require_text(external_ref, "external_ref", 100)
        item = self.repository.create_item(title, description, severity, quantity,
                                           threshold, external_ref, actor)
        self.repository.append_audit("create", ENTITY, item["id"], actor, {
            "title": title, "severity": severity, "quantity": quantity,
            "priority": priority_score(severity, quantity, threshold),
        })
        return self.enrich(item)

    def add_record(self, item_id: int, payload: Dict[str, Any], actor: str,
                   role: str) -> Dict[str, Any]:
        ensure_role(role, RECORD_ROLES)
        actor = require_text(actor, "actor", 100)
        kind = require_text(payload.get("kind"), "kind", 100)
        detail = require_text(payload.get("detail"), "detail")
        status = payload.get("status", "open")
        if status not in ("open", "closed"):
            raise ValueError("status必须是open或closed")
        external_ref = payload.get("external_ref")
        if external_ref is not None:
            external_ref = require_text(external_ref, "external_ref", 100)
        record = self.repository.add_record(item_id, kind, detail, status,
                                            external_ref, actor)
        self.repository.append_audit("record", ENTITY, item_id, actor, {
            "record_id": record["id"], "kind": kind, "status": status,
        })
        return record

    def transition(self, item_id: int, target: str, expected_version: int,
                   actor: str, role: str) -> Dict[str, Any]:
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        validate_transition(item["status"], target)
        ensure_role(role, role_for_transition(target))
        if not isinstance(expected_version, int) or expected_version < 1:
            raise ValueError("expected_version必须是正整数")
        blockers = completion_blockers(target, self.repository.open_record_count(item_id))
        if blockers:
            from .domain import ConflictError
            raise ConflictError("；".join(blockers))
        updated = self.repository.transition_item(item_id, target, expected_version, actor)
        self.repository.append_audit("transition", ENTITY, item_id, actor, {
            "from": item["status"], "to": target,
            "escalation_required": escalation_required(
                item["severity"], item["quantity"], item["threshold"]),
        })
        return self.enrich(updated)

    def get_item(self, item_id: int, role: str) -> Dict[str, Any]:
        self._view(role)
        return self.enrich(self.repository.get_item(item_id))

    def list_items(self, role: str, status: Optional[str] = None) -> list:
        self._view(role)
        return [self.enrich(item) for item in self.repository.list_items(status)]

    def list_records(self, item_id: int, role: str) -> list:
        self._view(role)
        return self.repository.list_records(item_id)

    def audit(self, role: str, item_id: Optional[int] = None) -> list:
        ensure_role(role, AUDIT_ROLES)
        return self.repository.list_audit(item_id)

    def create_batch(self, payload: Dict[str, Any], actor: str,
                     role: str) -> Dict[str, Any]:
        ensure_role(role, BATCH_CREATE_ROLES)
        actor = require_text(actor, "actor", 100)
        scope = require_text(payload.get("scope"), "scope", 200)
        planned_date = require_date(payload.get("planned_date"), "planned_date")
        item_ids = require_id_list(payload.get("item_ids"), "item_ids")
        for item_id in item_ids:
            self.repository.get_item(item_id)
        existing = self.repository.find_active_batch(item_ids)
        if existing is not None:
            batch = self._serialize_batch(existing)
            batch["reused"] = True
            return batch
        batch_row = self.repository.create_batch(scope, planned_date, item_ids, actor)
        self.repository.append_audit("batch_create", BATCH_ENTITY, batch_row["id"], actor, {
            "scope": scope, "planned_date": planned_date, "item_ids": item_ids,
        })
        batch = self._serialize_batch(batch_row)
        batch["reused"] = False
        return batch

    def get_batch(self, batch_id: int, role: str) -> Dict[str, Any]:
        ensure_role(role, BATCH_VIEW_ROLES)
        batch = self._serialize_batch(self.repository.get_batch(batch_id))
        batch["entries"] = self._batch_entries(batch_id)
        batch["blockers"] = batch_blockers(batch["entries"])
        batch["closeable"] = batch["status"] == ACTIVE_BATCH and not batch["blockers"]
        return batch

    def list_batches(self, role: str, state: Optional[str] = None) -> list:
        ensure_role(role, BATCH_VIEW_ROLES)
        from .domain import ValidationError
        if state in (None, ""):
            state = "active"
        if state in ("active", "in_progress", "closeable", "closed", "pending_close"):
            pass
        else:
            raise ValidationError("state只支持active/in_progress/closeable/closed")
        status = "closed" if state == "closed" else ACTIVE_BATCH
        batches = [self._serialize_batch(row) for row in self.repository.list_batches(status)]
        if state == "in_progress":
            batches = [b for b in batches if b["blocker_count"] > 0]
        elif state in ("closeable", "pending_close"):
            batches = [b for b in batches if b["blocker_count"] == 0]
        batches.sort(key=lambda b: (-b["priority"], b["remaining_days"], b["id"]))
        return batches

    def close_batch(self, batch_id: int, actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, BATCH_CLOSE_ROLES)
        actor = require_text(actor, "actor", 100)
        batch_row = self.repository.get_batch(batch_id)
        if batch_row["status"] != ACTIVE_BATCH:
            raise ConflictError("批次已关闭，不能重复关闭")
        entries = self._batch_entries(batch_id)
        blockers = batch_blockers(entries)
        if blockers:
            raise ConflictError(f"批次尚有{len(blockers)}个阻塞项目，总工无法关闭")
        updated = self.repository.close_batch(batch_id, actor)
        self.repository.append_audit("batch_close", BATCH_ENTITY, batch_id, actor, {
            "item_count": len(entries),
        })
        return self._serialize_batch(updated)

    def _batch_entries(self, batch_id: int) -> list:
        entries = self.repository.batch_item_entries(batch_id)
        result = []
        for entry in entries:
            item = self.repository.get_item(entry["item_id"])
            result.append({
                "item_id": entry["item_id"],
                "title": entry["title"],
                "status": entry["status"],
                "terminal": entry["status"] in TERMINAL_STATES,
                "open_records": int(entry["open_records"]),
                "item_priority": priority_score(
                    item["severity"], item["quantity"], item["threshold"],
                    int(entry["open_records"])),
            })
        return result

    def _serialize_batch(self, batch_row: Dict[str, Any]) -> Dict[str, Any]:
        entries = self.repository.batch_item_entries(batch_row["id"])
        max_item_priority = 0
        for entry in entries:
            item = self.repository.get_item(entry["item_id"])
            score = priority_score(item["severity"], item["quantity"], item["threshold"],
                                   int(entry["open_records"]))
            max_item_priority = max(max_item_priority, score)
        total_open = sum(int(e["open_records"]) for e in entries)
        remaining = days_remaining(batch_row["planned_date"], date.today())
        result = dict(batch_row)
        result["item_count"] = len(entries)
        result["remaining_days"] = remaining
        result["overdue"] = remaining < 0 and batch_row["status"] == ACTIVE_BATCH
        result["priority"] = batch_priority(max_item_priority, total_open, remaining)
        result["blocker_count"] = len(batch_blockers([
            {"item_id": e["item_id"], "title": e["title"],
             "status": e["status"], "open_records": int(e["open_records"])}
            for e in entries
        ]))
        return result

    @staticmethod
    def enrich(item: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(item)
        result["priority"] = priority_score(
            item["severity"], item["quantity"], item["threshold"])
        result["deadline_hours"] = response_deadline_hours(
            item["severity"], item["quantity"], item["threshold"])
        result["escalation_required"] = escalation_required(
            item["severity"], item["quantity"], item["threshold"])
        return result
