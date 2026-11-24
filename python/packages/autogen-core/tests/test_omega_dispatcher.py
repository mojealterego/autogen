import unittest
from autogen_core._omega_dispatcher import DispatchContext, ForensicToolDispatcher
from autogen_core._signal_intelligence import SignalEvent


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.dispatcher = ForensicToolDispatcher({"a:case1": frozenset({"imei_structure_check", "exif_metadata_check", "signal_event_correlation"})})
        self.context = DispatchContext("a", "case1")

    def test_imei_offline(self):
        result = self.dispatcher.execute(self.context, "imei_structure_check", {"imei": "490154203237518"})
        self.assertTrue(result.valid)
        self.assertEqual(result.reason, "valid_structure_only")

    def test_exif_offline(self):
        result = self.dispatcher.execute(self.context, "exif_metadata_check", {"expected_serial": "ABC123", "metadata": {"SerialNumber": "ABC123"}})
        self.assertEqual(result.status, "match")

    def test_signal_correlation(self):
        events = (SignalEvent("a", "s", "rf", 1, "x", 7), SignalEvent("a", "s", "usb", 2, "x", 7))
        result = self.dispatcher.execute(self.context, "signal_event_correlation", {"events": events, "window_ms": 5000})
        self.assertEqual(result.classification, "review")

    def test_cross_tenant_denied(self):
        with self.assertRaises(PermissionError):
            self.dispatcher.execute(DispatchContext("b", "case1"), "imei_structure_check", {"imei": "490154203237518"})

    def test_unregistered_tool_denied(self):
        with self.assertRaises(PermissionError):
            self.dispatcher.execute(self.context, "fbe_bypass", {})

    def test_payload_strict(self):
        with self.assertRaises(ValueError):
            self.dispatcher.execute(self.context, "imei_structure_check", {"imei": "490154203237518", "command": "shell"})

    def test_cross_tenant_signal_denied(self):
        event = SignalEvent("b", "s", "rf", 1, "x", 7)
        with self.assertRaises(PermissionError):
            self.dispatcher.execute(self.context, "signal_event_correlation", {"events": (event,), "window_ms": 5000})


if __name__ == "__main__":
    unittest.main()
