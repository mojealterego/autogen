import unittest
from autogen_core._omega_agentchat_adapter import build_forensic_tools
from autogen_core._omega_dispatcher import DispatchContext, ForensicToolDispatcher
from autogen_core._omega_execution_guard import GuardedExecution

class AdapterTests(unittest.TestCase):
    def test_invalid_guard(self):
        with self.assertRaises(TypeError): build_forensic_tools(None, DispatchContext("a","c"))
    def test_invalid_context(self):
        guard=GuardedExecution(ForensicToolDispatcher({}))
        with self.assertRaises(TypeError): build_forensic_tools(guard, None)
    def test_tool_registration(self):
        guard=GuardedExecution(ForensicToolDispatcher({"a:c":frozenset({"imei_structure_check"})}))
        tools=build_forensic_tools(guard, DispatchContext("a","c"))
        self.assertEqual({t.name for t in tools},{"imei_structure_check","exif_metadata_check"})
if __name__=="__main__": unittest.main()
