"""Policy tests for the Magnetic Zero agent tool gateway."""
import unittest
from autogen_core._omega_tool_policy import ToolPolicy, ToolRequest, Decision

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = ToolPolicy()

    def test_read_only_requires_tenant_and_scope(self):
        request = ToolRequest('tenant-a','imei_structure_check','case-1')
        self.assertEqual(self.policy.evaluate(request, {'tenant-a:case-1': frozenset({'imei_structure_check'})}).decision, Decision.ALLOW)
        self.assertEqual(self.policy.evaluate(request, {}).decision, Decision.DENY)

    def test_unknown_tool_fails_closed(self):
        self.assertEqual(self.policy.evaluate(ToolRequest('tenant-a','arbitrary_shell','case-1'), {'tenant-a:case-1': frozenset({'arbitrary_shell'})}).decision, Decision.DENY)

    def test_active_operations_are_never_executable(self):
        for tool in ('bluetooth_exploit','fbe_bypass','evil_twin','arp_spoof','credential_reset'):
            result = self.policy.evaluate(ToolRequest('tenant-a',tool,'case-1'), {'tenant-a:case-1': frozenset({tool})})
            self.assertEqual(result.decision, Decision.DENY)

    def test_invalid_request(self):
        with self.assertRaises(ValueError):
            ToolRequest('../tenant','imei_structure_check','case-1')
        with self.assertRaises(ValueError):
            ToolRequest('tenant-a','imei_structure_check','')

    def test_approval_cannot_override_prohibited_action(self):
        request = ToolRequest('tenant-a','fbe_bypass','case-1',approved=True)
        self.assertEqual(self.policy.evaluate(request, {'tenant-a:case-1': frozenset({'fbe_bypass'})}).decision, Decision.DENY)

    def test_scope_is_exact_and_tenant_bound(self):
        request = ToolRequest('tenant-a','exif_metadata_check','case-1')
        self.assertEqual(self.policy.evaluate(request, {'tenant-a:case-1': frozenset({'exif_metadata_check'})}).decision, Decision.ALLOW)
        self.assertEqual(self.policy.evaluate(request, {'tenant-a:case-1': frozenset({'imei_structure_check'})}).decision, Decision.DENY)
        self.assertEqual(self.policy.evaluate(request, {'tenant-b:case-1': frozenset({'exif_metadata_check'})}).decision, Decision.DENY)

if __name__ == '__main__':
    unittest.main()
