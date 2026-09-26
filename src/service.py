from __future__ import annotations

from typing import Any, Dict, Optional

from .domain import (ValidationError, ensure_role, normalize_severity,
                     require_number, require_text)
from .repository import Repository
from .rules import (AUDIT_ROLES, CREATE_ROLES, ENTITY, RECORD_ROLES,
                    TICKET_CONFIRM_ROLES, TICKET_CREATE_ROLES, TITLE,
                    VIEW_ROLES, completion_blockers, ensure_confirmation_stage,
                    ensure_ticket_registration, escalation_required,
                    evaluate_confirmation, priority_score,
                    response_deadline_hours, role_for_transition,
                    ticket_blockers, validate_ticket_plan, validate_transition)


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

    def create_ticket(self, item_id: int, payload: Dict[str, Any], actor: str,
                      role: str) -> Dict[str, Any]:
        ensure_role(role, TICKET_CREATE_ROLES)
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        ensure_ticket_registration(item["status"])
        gate_seq = payload.get("gate_seq")
        if isinstance(gate_seq, bool) or not isinstance(gate_seq, int) or gate_seq < 1:
            raise ValidationError("gate_seq必须是正整数")
        planned_opening = require_number(
            payload.get("planned_opening"), "planned_opening", 0.000001)
        executor = require_text(payload.get("executor"), "executor", 100)
        reviewer = require_text(payload.get("reviewer"), "reviewer", 100)
        level_min = require_number(payload.get("level_min"), "level_min")
        flow_max = require_number(payload.get("flow_max"), "flow_max", 0.000001)
        validate_ticket_plan(executor, reviewer)
        ticket = self.repository.create_ticket(
            item_id, gate_seq, planned_opening, executor, reviewer, level_min,
            flow_max, actor)
        self.repository.append_audit("ticket_create", ENTITY, item_id, actor, {
            "ticket_id": ticket["id"], "gate_seq": gate_seq, "executor": executor,
            "reviewer": reviewer, "planned_opening": planned_opening,
        })
        return ticket

    def confirm_ticket(self, item_id: int, ticket_id: int, payload: Dict[str, Any],
                       actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, TICKET_CONFIRM_ROLES)
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        ticket = self.repository.get_item_ticket(item_id, ticket_id)
        ensure_confirmation_stage(item["status"])
        if ticket["status"] == "confirmed":
            from .domain import ConflictError
            raise ConflictError("票据已确认")
        actual_level = require_number(payload.get("actual_level"), "actual_level")
        actual_flow = require_number(payload.get("actual_flow"), "actual_flow")
        decision, reason = evaluate_confirmation(
            ticket["reviewer"], actor, actual_level, actual_flow,
            ticket["level_min"], ticket["flow_max"])
        detail = {"ticket_id": ticket_id, "gate_seq": ticket["gate_seq"],
                  "actual_level": actual_level, "actual_flow": actual_flow}
        if decision == "confirmed":
            updated = self.repository.confirm_ticket(
                ticket_id, actual_level, actual_flow, actor)
            self.repository.append_audit("ticket_confirm", ENTITY, item_id, actor, detail)
        else:
            updated = self.repository.mark_ticket_redo(ticket_id)
            self.repository.append_audit("ticket_redo", ENTITY, item_id, actor,
                                         dict(detail, reason=reason))
        return updated

    def transition(self, item_id: int, target: str, expected_version: int,
                   actor: str, role: str) -> Dict[str, Any]:
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        validate_transition(item["status"], target)
        ensure_role(role, role_for_transition(target))
        if not isinstance(expected_version, int) or expected_version < 1:
            raise ValueError("expected_version必须是正整数")
        blockers = completion_blockers(target, self.repository.open_record_count(item_id))
        blockers += ticket_blockers(target, self.repository.pending_ticket_count(item_id))
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

    def list_tickets(self, item_id: int, role: str) -> list:
        self._view(role)
        return self.repository.list_tickets(item_id)

    def audit(self, role: str, item_id: Optional[int] = None) -> list:
        ensure_role(role, AUDIT_ROLES)
        return self.repository.list_audit(item_id)

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
