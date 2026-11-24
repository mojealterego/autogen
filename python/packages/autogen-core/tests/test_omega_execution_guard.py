"""Deterministic tests for ten acceptance checkpoints of the offline execution guard."""
import unittest
from autogen_core._omega_execution_guard import GuardedExecution, ExecutionError
from autogen_core._omega_dispatcher import DispatchContext, ForensicToolDispatcher


class GuardTests(unittest.TestCase):
    def setUp(self):
        dispatcher = ForensicToolDispatcher({"a:case": frozenset({"imei_structure_check"})})
        self.guard = GuardedExecution(dispatcher, max_calls=2, max_payload_bytes=512)
        self.context = DispatchContext("a", "case")
        self.payload = {"imei": "490154203237518"}

    def test_01_dispatch(self):
        self.assertTrue(self.guard.call(self.context, "imei_structure_check", self.payload, request_id="r1").valid)

    def test_02_reject_unauthorized(self):
        with self.assertRaises(PermissionError):
            self.guard.call(self.context, "fbe_bypass", {}, request_id="r1")

    def test_03_cross_tenant(self):
        with self.assertRaises(PermissionError):
            self.guard.call(DispatchContext("b", "case"), "imei_structure_check", self.payload, request_id="r1")

    def test_04_dedup_same_request(self):
        first = self.guard.call(self.context, "imei_structure_check", self.payload, request_id="r1")
        self.assertEqual(first, self.guard.call(self.context, "imei_structure_check", self.payload, request_id="r1"))
        self.assertEqual(self.guard.count("a", "case"), 1)

    def test_05_conflicting_request_id(self):
        self.guard.call(self.context, "imei_structure_check", self.payload, request_id="r1")
        with self.assertRaises(ExecutionError):
            self.guard.call(self.context, "imei_structure_check", {"imei": "000000000000000"}, request_id="r1")

    def test_06_quota(self):
        for i in range(2):
            self.guard.call(self.context, "imei_structure_check", self.payload, request_id=f"r{i}")
        with self.assertRaises(ExecutionError):
            self.guard.call(self.context, "imei_structure_check", self.payload, request_id="r3")

    def test_07_payload_budget(self):
        with self.assertRaises(ExecutionError):
            self.guard.call(self.context, "imei_structure_check", {"imei": "1" * 1000}, request_id="r1")

    def test_08_audit_chain(self):
        self.guard.call(self.context, "imei_structure_check", self.payload, request_id="r1")
        self.assertTrue(self.guard.verify_audit())
        self.assertEqual(len(self.guard.audit_records()), 1)

    def test_09_rejected_calls_not_charged(self):
        with self.assertRaises(PermissionError):
            self.guard.call(self.context, "evil_twin", {}, request_id="x")
        self.assertEqual(self.guard.count("a", "case"), 0)

    def test_10_reject_invalid_ids(self):
        with self.assertRaises(ValueError):
            self.guard.call(self.context, "imei_structure_check", self.payload, request_id="../bad")


if __name__ == "__main__":
    unittest.main()
