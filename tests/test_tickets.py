import tempfile, unittest
from pathlib import Path
from src.domain import ConflictError, PermissionDenied, ValidationError
from src.repository import Repository
from src.service import Service
from src.rules import STATES, TRANSITION_ROLES


class TicketTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(str(Path(self.tmp.name) / "test.db"))
        self.service = Service(self.repo)
        self.item = self.service.create_item(
            {"title": "flood gate dispatch", "description": "flood season gate operation",
             "severity": "urgent", "quantity": 12, "threshold": 6,
             "external_ref": "GATE-1"}, "creator", "duty_officer")

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def _advance(self, item, targets):
        current = item
        for target in targets:
            current = self.service.transition(
                current["id"], target, current["version"], "reviewer",
                TRANSITION_ROLES[target][0])
        return current

    def _ticket_payload(self, **overrides):
        payload = {"sequence": 1, "planned_opening": 0.5, "executor": "op1",
                   "reviewer": "rev1", "level_lower": 100.0, "flow_upper": 500.0}
        payload.update(overrides)
        return payload

    def _authorized(self):
        return self._advance(self.item, ["checked", "authorized"])

    def _executed_with_ticket(self, **overrides):
        item = self._authorized()
        ticket = self.service.register_ticket(
            item["id"], self._ticket_payload(**overrides), "dispatcher1", "dispatcher")
        return self._advance(item, ["executed"]), ticket

    def test_register_requires_authorized_item_and_dispatcher(self):
        with self.assertRaises(ConflictError):
            self.service.register_ticket(
                self.item["id"], self._ticket_payload(), "dispatcher1", "dispatcher")
        item = self._authorized()
        with self.assertRaises(PermissionDenied):
            self.service.register_ticket(
                item["id"], self._ticket_payload(), "officer1", "duty_officer")
        ticket = self.service.register_ticket(
            item["id"], self._ticket_payload(), "dispatcher1", "dispatcher")
        self.assertEqual(ticket["status"], "registered")
        self.assertEqual(ticket["sequence"], 1)

    def test_same_account_cannot_execute_and_review(self):
        item = self._authorized()
        with self.assertRaises(ValidationError):
            self.service.register_ticket(
                item["id"], self._ticket_payload(executor="rev1"), "dispatcher1",
                "dispatcher")

    def test_sequence_must_be_unique_per_item(self):
        item = self._authorized()
        self.service.register_ticket(
            item["id"], self._ticket_payload(), "dispatcher1", "dispatcher")
        with self.assertRaises(ConflictError):
            self.service.register_ticket(
                item["id"], self._ticket_payload(reviewer="rev2"), "dispatcher1",
                "dispatcher")
        other = self.service.register_ticket(
            item["id"], self._ticket_payload(sequence=2, reviewer="rev2"),
            "dispatcher1", "dispatcher")
        self.assertEqual(other["sequence"], 2)

    def test_confirm_requires_executed_item(self):
        item = self._authorized()
        ticket = self.service.register_ticket(
            item["id"], self._ticket_payload(), "dispatcher1", "dispatcher")
        with self.assertRaises(ConflictError):
            self.service.confirm_ticket(
                item["id"], ticket["id"],
                {"field_level": 101.0, "actual_flow": 400.0}, "rev1", "dispatcher")

    def test_full_confirmation_flow_allows_archive(self):
        item, ticket = self._executed_with_ticket()
        confirmed = self.service.confirm_ticket(
            item["id"], ticket["id"], {"field_level": 101.0, "actual_flow": 400.0},
            "rev1", "dispatcher")
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(confirmed["confirmed_by"], "rev1")
        closed = self._advance(item, ["closed"])
        self.assertEqual(closed["status"], STATES[-1])
        self.assertTrue(self.repo.verify_audit_chain())

    def test_unconfirmed_ticket_blocks_archive(self):
        item, ticket = self._executed_with_ticket()
        with self.assertRaises(ConflictError):
            self.service.transition(item["id"], "closed", item["version"], "chief",
                                    "chief_engineer")

    def test_out_of_bounds_reading_goes_pending_redo_and_keeps_values(self):
        item, ticket = self._executed_with_ticket()
        updated = self.service.confirm_ticket(
            item["id"], ticket["id"], {"field_level": 99.0, "actual_flow": 400.0},
            "rev1", "dispatcher")
        self.assertEqual(updated["status"], "pending_redo")
        self.assertEqual(updated["field_level"], 99.0)
        self.assertEqual(updated["actual_flow"], 400.0)
        updated = self.service.confirm_ticket(
            item["id"], ticket["id"], {"field_level": 101.0, "actual_flow": 600.0},
            "rev1", "dispatcher")
        self.assertEqual(updated["status"], "pending_redo")
        self.assertEqual(updated["actual_flow"], 600.0)
        with self.assertRaises(ConflictError):
            self.service.transition(item["id"], "closed", item["version"], "chief",
                                    "chief_engineer")
        redone = self.service.confirm_ticket(
            item["id"], ticket["id"], {"field_level": 102.0, "actual_flow": 450.0},
            "rev1", "dispatcher")
        self.assertEqual(redone["status"], "confirmed")
        self._advance(item, ["closed"])

    def test_reviewer_shift_change_goes_pending_redo_then_revise(self):
        item, ticket = self._executed_with_ticket()
        updated = self.service.confirm_ticket(
            item["id"], ticket["id"], {"field_level": 101.0, "actual_flow": 400.0},
            "rev2", "dispatcher")
        self.assertEqual(updated["status"], "pending_redo")
        self.assertEqual(updated["confirmed_by"], "rev2")
        revised = self.service.revise_ticket(
            item["id"], ticket["id"], {"reviewer": "rev2"}, "dispatcher1", "dispatcher")
        self.assertEqual(revised["status"], "registered")
        self.assertEqual(revised["reviewer"], "rev2")
        self.assertEqual(revised["field_level"], 101.0)
        confirmed = self.service.confirm_ticket(
            item["id"], ticket["id"], {"field_level": 101.0, "actual_flow": 400.0},
            "rev2", "duty_officer")
        self.assertEqual(confirmed["status"], "confirmed")
        self._advance(item, ["closed"])
        self.assertTrue(self.repo.verify_audit_chain())

    def test_confirmed_ticket_is_immutable(self):
        item, ticket = self._executed_with_ticket()
        self.service.confirm_ticket(
            item["id"], ticket["id"], {"field_level": 101.0, "actual_flow": 400.0},
            "rev1", "dispatcher")
        with self.assertRaises(ConflictError):
            self.service.confirm_ticket(
                item["id"], ticket["id"],
                {"field_level": 101.0, "actual_flow": 400.0}, "rev1", "dispatcher")
        with self.assertRaises(ConflictError):
            self.service.revise_ticket(
                item["id"], ticket["id"], {"reviewer": "rev2"}, "dispatcher1",
                "dispatcher")

    def test_list_tickets_and_audit_trail(self):
        item, ticket = self._executed_with_ticket()
        self.service.register_ticket(
            item["id"], self._ticket_payload(sequence=2, reviewer="rev2"),
            "dispatcher1", "dispatcher")
        tickets = self.service.list_tickets(item["id"], "viewer")
        self.assertEqual([t["sequence"] for t in tickets], [1, 2])
        self.service.confirm_ticket(
            item["id"], ticket["id"], {"field_level": 101.0, "actual_flow": 400.0},
            "rev1", "dispatcher")
        events = self.service.audit("viewer")
        actions = [e["action"] for e in events]
        self.assertIn("ticket_register", actions)
        self.assertIn("ticket_confirm", actions)
        self.assertTrue(self.repo.verify_audit_chain())


if __name__ == "__main__":
    unittest.main()
