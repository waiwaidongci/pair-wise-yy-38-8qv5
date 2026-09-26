from __future__ import annotations

from typing import Any, Dict, Optional

from .domain import (ConflictError, ensure_role, normalize_severity, require_int,
                     require_number, require_text)
from .repository import Repository
from .rules import (AUDIT_ROLES, CREATE_ROLES, ENTITY, RECORD_ROLES,
                    TERMINAL_STATES, TICKET_CONFIRM_ITEM_STATES,
                    TICKET_CONFIRM_ROLES, TICKET_CREATE_ROLES, TICKET_ENTITY,
                    TICKET_REGISTER_ITEM_STATES, TICKET_REVISE_ROLES, TITLE,
                    VIEW_ROLES, completion_blockers, escalation_required,
                    priority_score, response_deadline_hours, role_for_transition,
                    ticket_archive_blockers, ticket_confirmation_outcome,
                    validate_ticket_plan, validate_transition)


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

    @staticmethod
    def _ticket_plan(payload: Dict[str, Any], base: Optional[Dict[str, Any]] = None):
        base = base or {}
        sequence = require_int(payload.get("sequence", base.get("sequence")), "sequence")
        planned_opening = require_number(
            payload.get("planned_opening", base.get("planned_opening")),
            "planned_opening", 0.000001)
        executor = require_text(payload.get("executor", base.get("executor")),
                                "executor", 100)
        reviewer = require_text(payload.get("reviewer", base.get("reviewer")),
                                "reviewer", 100)
        level_lower = require_number(
            payload.get("level_lower", base.get("level_lower")), "level_lower")
        flow_upper = require_number(
            payload.get("flow_upper", base.get("flow_upper")), "flow_upper", 0.000001)
        validate_ticket_plan(executor, reviewer)
        return sequence, planned_opening, executor, reviewer, level_lower, flow_upper

    def register_ticket(self, item_id: int, payload: Dict[str, Any], actor: str,
                        role: str) -> Dict[str, Any]:
        ensure_role(role, TICKET_CREATE_ROLES)
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        if item["status"] not in TICKET_REGISTER_ITEM_STATES:
            raise ConflictError("当前状态不能登记操作票据")
        sequence, planned_opening, executor, reviewer, level_lower, flow_upper = \
            self._ticket_plan(payload)
        ticket = self.repository.create_ticket(item_id, sequence, planned_opening,
                                               executor, reviewer, level_lower,
                                               flow_upper, actor)
        self.repository.append_audit("ticket_register", TICKET_ENTITY, ticket["id"],
                                     actor, {
                                         "item_id": item_id, "sequence": sequence,
                                         "planned_opening": planned_opening,
                                         "executor": executor, "reviewer": reviewer,
                                     })
        return ticket

    def revise_ticket(self, item_id: int, ticket_id: int, payload: Dict[str, Any],
                      actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, TICKET_REVISE_ROLES)
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        if item["status"] in TERMINAL_STATES:
            raise ConflictError("指令已归档，不能修改操作票据")
        ticket = self.repository.get_item_ticket(item_id, ticket_id)
        if ticket["status"] == "confirmed":
            raise ConflictError("票据已确认，不能修改")
        sequence, planned_opening, executor, reviewer, level_lower, flow_upper = \
            self._ticket_plan(payload, ticket)
        updated = self.repository.revise_ticket(ticket_id, sequence, planned_opening,
                                                executor, reviewer, level_lower,
                                                flow_upper, actor)
        self.repository.append_audit("ticket_revise", TICKET_ENTITY, ticket_id, actor, {
            "item_id": item_id, "sequence": sequence, "executor": executor,
            "reviewer": reviewer, "previous_status": ticket["status"],
        })
        return updated

    def confirm_ticket(self, item_id: int, ticket_id: int, payload: Dict[str, Any],
                       actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, TICKET_CONFIRM_ROLES)
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        if item["status"] not in TICKET_CONFIRM_ITEM_STATES:
            raise ConflictError("指令未执行，不能确认操作票据")
        ticket = self.repository.get_item_ticket(item_id, ticket_id)
        if ticket["status"] == "confirmed":
            raise ConflictError("票据已确认")
        field_level = require_number(payload.get("field_level"), "field_level")
        actual_flow = require_number(payload.get("actual_flow"), "actual_flow")
        status, reasons = ticket_confirmation_outcome(
            field_level, actual_flow, ticket["level_lower"], ticket["flow_upper"],
            ticket["reviewer"], actor)
        updated = self.repository.record_confirmation(ticket_id, field_level,
                                                      actual_flow, actor, status)
        self.repository.append_audit("ticket_confirm", TICKET_ENTITY, ticket_id,
                                     actor, {
                                         "item_id": item_id,
                                         "sequence": ticket["sequence"],
                                         "result": status, "reasons": reasons,
                                         "field_level": field_level,
                                         "actual_flow": actual_flow,
                                     })
        return updated

    def list_tickets(self, item_id: int, role: str) -> list:
        self._view(role)
        return self.repository.list_tickets(item_id)

    def transition(self, item_id: int, target: str, expected_version: int,
                   actor: str, role: str) -> Dict[str, Any]:
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        validate_transition(item["status"], target)
        ensure_role(role, role_for_transition(target))
        if not isinstance(expected_version, int) or expected_version < 1:
            raise ValueError("expected_version必须是正整数")
        blockers = completion_blockers(target, self.repository.open_record_count(item_id))
        blockers += ticket_archive_blockers(
            target, self.repository.unconfirmed_ticket_count(item_id))
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
