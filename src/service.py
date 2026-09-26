from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .domain import (ConflictError, ValidationError, ensure_role,
                     normalize_severity, require_date, require_number,
                     require_text)
from .repository import Repository
from .rules import (AUDIT_ROLES, BATCH_CLOSE_ROLES, BATCH_CREATE_ROLES,
                    BATCH_ENTITY, BATCH_LIST_STATES, CREATE_ROLES, ENTITY,
                    RECORD_ROLES, TITLE, VIEW_ROLES, batch_close_blockers,
                    batch_list_state, batch_priority, completion_blockers,
                    escalation_required, priority_score,
                    response_deadline_hours, role_for_transition,
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
        scope = require_text(payload.get("scope"), "scope", 500)
        planned = require_date(payload.get("planned_review_date"),
                               "planned_review_date")
        item_ids = payload.get("item_ids")
        if not isinstance(item_ids, list) or not item_ids:
            raise ValidationError("item_ids必须是非空数组")
        normalized = []
        for raw in item_ids:
            if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
                raise ValidationError("item_ids必须是正整数数组")
            if raw not in normalized:
                normalized.append(raw)
        for item_id in normalized:
            self.repository.get_item(item_id)
        batch, created = self.repository.create_batch(scope, planned,
                                                      normalized, actor)
        if created:
            self.repository.append_audit("batch_create", BATCH_ENTITY,
                                         batch["id"], actor, {
                                             "scope": scope,
                                             "planned_review_date": planned,
                                             "item_ids": normalized,
                                         })
        result = self.enrich_batch(batch)
        result["created"] = created
        return result

    def get_batch(self, batch_id: int, role: str) -> Dict[str, Any]:
        self._view(role)
        return self.enrich_batch(self.repository.get_batch(batch_id))

    def list_batches(self, role: str, state: Optional[str] = None) -> list:
        self._view(role)
        if state is not None and state not in BATCH_LIST_STATES:
            raise ValidationError("state必须是active、closable或closed")
        batches = [self.enrich_batch(batch)
                   for batch in self.repository.list_batches()]
        if state:
            batches = [b for b in batches if b["list_state"] == state]
        return batches

    def close_batch(self, batch_id: int, expected_version: int, actor: str,
                    role: str) -> Dict[str, Any]:
        ensure_role(role, BATCH_CLOSE_ROLES)
        actor = require_text(actor, "actor", 100)
        if not isinstance(expected_version, int) or isinstance(
                expected_version, bool) or expected_version < 1:
            raise ValidationError("expected_version必须是正整数")
        batch = self.repository.get_batch(batch_id)
        if batch["status"] == "closed":
            raise ConflictError("批次已关闭")
        items = self.repository.batch_items(batch_id)
        blockers = batch_close_blockers(items)
        if blockers:
            summary = "；".join(
                f"#{b['item_id']} {b['title']}（{'，'.join(b['reasons'])}）"
                for b in blockers)
            raise ConflictError(f"批次存在阻塞项目：{summary}")
        updated = self.repository.close_batch(batch_id, expected_version, actor)
        self.repository.append_audit("batch_close", BATCH_ENTITY, batch_id,
                                     actor, {"item_count": len(items)})
        return self.enrich_batch(updated)

    def enrich_batch(self, batch: Dict[str, Any]) -> Dict[str, Any]:
        items = self.repository.batch_items(batch["id"])
        now = datetime.now(timezone.utc)
        enriched_items = []
        priorities = []
        remaining = []
        open_records = 0
        for item in items:
            enriched = self.enrich(item)
            open_records += item["open_records"]
            priorities.append(enriched["priority"])
            try:
                created = datetime.fromisoformat(item["created_at"])
                elapsed = (now - created).total_seconds() / 3600.0
            except ValueError:
                elapsed = 0.0
            remaining.append(enriched["deadline_hours"] - max(0.0, elapsed))
            enriched_items.append(enriched)
        blockers = batch_close_blockers(items)
        result = dict(batch)
        result["items"] = enriched_items
        result["item_count"] = len(items)
        result["open_records"] = open_records
        result["blockers"] = blockers
        result["priority"] = batch_priority(priorities, open_records, remaining)
        result["list_state"] = batch_list_state(batch["status"], blockers)
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
