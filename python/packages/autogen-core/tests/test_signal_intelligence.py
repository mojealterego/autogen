import unittest
from autogen_core._signal_intelligence import SignalEvent, correlate_events

def event(domain="rf", timestamp=100, severity=7, tenant="a"):
    return SignalEvent(tenant, "sensor", domain, timestamp, "anomaly", severity)

class SignalIntelligenceTests(unittest.TestCase):
    def test_cross_domain_review_order_invariant(self):
        a, b = event(), event("usb", 102)
        x = correlate_events((a, b), tenant_id="a")
        self.assertEqual(x, correlate_events((b, a), tenant_id="a"))
        self.assertEqual(x.classification, "review")

    def test_single_domain_not_proof(self):
        self.assertEqual(correlate_events((event(severity=10), event(timestamp=101, severity=10)), tenant_id="a").classification, "insufficient_evidence")

    def test_cross_tenant_denied(self):
        with self.assertRaises(PermissionError):
            correlate_events((event(tenant="b"),), tenant_id="a")

    def test_budget(self):
        with self.assertRaises(ValueError):
            correlate_events((event(),) * 1025, tenant_id="a")

    def test_window(self):
        self.assertEqual(correlate_events((event(), event("usb", 9999)), tenant_id="a").classification, "insufficient_evidence")

    def test_invalid_severity(self):
        with self.assertRaises(ValueError):
            event(severity=11)

    def test_empty(self):
        self.assertEqual(correlate_events((), tenant_id="a").score, 0)

if __name__ == "__main__":
    unittest.main()
