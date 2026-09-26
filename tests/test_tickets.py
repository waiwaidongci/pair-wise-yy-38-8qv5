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
            {"title": "flood gate release", "description": "gate ticket scenarios",
             "severity": "urgent", "quantity": 12, "threshold": 6,
             "external_ref": "GT-1"}, "creator", "duty_officer")

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def _advance(self, targets):
        current = self.service.get_item(self.item["id"], "viewer")
        for target in targets:
            current = self.service.transition(
                current["id"], target, current["version"], "reviewer",
                TRANSITION_ROLES[target][0])
        return current

    def _payload(self, seq=1, executor="operator-1", reviewer="reviewer-1"):
        return {"gate_seq": seq, "planned_opening": 0.5, "executor": executor,
                "reviewer": reviewer, "level_min": 100.0, "flow_max": 500.0}

    def test_registration_rules(self):
        with self.assertRaises(PermissionDenied):
            self.service.create_ticket(
                self.item["id"], self._payload(), "dispatcher-1", "viewer")
        with self.assertRaises(ValidationError):
            self.service.create_ticket(
                self.item["id"], self._payload(reviewer="operator-1"),
                "dispatcher-1", "dispatcher")
        ticket = self.service.create_ticket(
            self.item["id"], self._payload(), "dispatcher-1", "dispatcher")
        self.assertEqual(ticket["status"], "registered")
        with self.assertRaises(ConflictError):
            self.service.create_ticket(
                self.item["id"], self._payload(), "dispatcher-1", "dispatcher")

    def test_confirmation_redo_and_retry(self):
        ticket = self.service.create_ticket(
            self.item["id"], self._payload(), "dispatcher-1", "dispatcher")
        with self.assertRaises(ConflictError):
            self.service.confirm_ticket(
                self.item["id"], ticket["id"],
                {"actual_level": 101.0, "actual_flow": 400.0},
                "reviewer-1", "dispatcher")
        self._advance(STATES[1:4])
        shifted = self.service.confirm_ticket(
            self.item["id"], ticket["id"],
            {"actual_level": 101.0, "actual_flow": 400.0},
            "reviewer-2", "dispatcher")
        self.assertEqual(shifted["status"], "redo")
        self.assertIsNone(shifted["actual_level"])
        self.assertEqual(shifted["planned_opening"], 0.5)
        overflow = self.service.confirm_ticket(
            self.item["id"], ticket["id"],
            {"actual_level": 99.0, "actual_flow": 600.0},
            "reviewer-1", "dispatcher")
        self.assertEqual(overflow["status"], "redo")
        self.assertIsNone(overflow["actual_flow"])
        confirmed = self.service.confirm_ticket(
            self.item["id"], ticket["id"],
            {"actual_level": 101.0, "actual_flow": 400.0},
            "reviewer-1", "dispatcher")
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(confirmed["actual_level"], 101.0)
        self.assertEqual(confirmed["confirmed_by"], "reviewer-1")
        with self.assertRaises(ConflictError):
            self.service.confirm_ticket(
                self.item["id"], ticket["id"],
                {"actual_level": 101.0, "actual_flow": 400.0},
                "reviewer-1", "dispatcher")

    def test_archive_requires_all_confirmed(self):
        first = self.service.create_ticket(
            self.item["id"], self._payload(seq=1), "dispatcher-1", "dispatcher")
        second = self.service.create_ticket(
            self.item["id"], self._payload(seq=2, reviewer="reviewer-2"),
            "dispatcher-1", "dispatcher")
        current = self._advance(STATES[1:4])
        self.service.confirm_ticket(
            self.item["id"], first["id"],
            {"actual_level": 101.0, "actual_flow": 400.0}, "reviewer-1", "dispatcher")
        with self.assertRaises(ConflictError):
            self.service.transition(
                current["id"], STATES[-1], current["version"], "closer",
                TRANSITION_ROLES[STATES[-1]][0])
        self.service.confirm_ticket(
            self.item["id"], second["id"],
            {"actual_level": 102.0, "actual_flow": 300.0}, "reviewer-2", "dispatcher")
        current = self.service.get_item(self.item["id"], "viewer")
        closed = self.service.transition(
            current["id"], STATES[-1], current["version"], "closer",
            TRANSITION_ROLES[STATES[-1]][0])
        self.assertEqual(closed["status"], STATES[-1])
        with self.assertRaises(ConflictError):
            self.service.create_ticket(
                self.item["id"], self._payload(seq=3), "dispatcher-1", "dispatcher")
        self.assertTrue(self.repo.verify_audit_chain())


if __name__ == "__main__":
    unittest.main()
