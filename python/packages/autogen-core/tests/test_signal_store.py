import tempfile
import unittest
from pathlib import Path
from autogen_core._signal_intelligence import SignalEvent
from autogen_core._signal_ingest import SourcePolicy
from autogen_core._signal_store import SignalJournal

class SignalJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'events.db'
        self.policies = {'rf-a': SourcePolicy('a', frozenset({'rf'})), 'usb-b': SourcePolicy('b', frozenset({'usb'}))}
    def tearDown(self): self.temp.cleanup()
    def test_restart_and_idempotency(self):
        e = SignalEvent('a','rf-a','rf',100,'anomaly',7)
        with SignalJournal(self.path,self.policies) as store:
            self.assertEqual(store.append(e),store.append(e))
            self.assertEqual(store.count('a'),1)
        with SignalJournal(self.path,self.policies) as store:
            self.assertEqual(store.read('a'),(e,))
            self.assertEqual(store.count('a'),1)
    def test_tenant_isolation(self):
        with SignalJournal(self.path,self.policies) as store:
            store.append(SignalEvent('a','rf-a','rf',1,'x',1))
            store.append(SignalEvent('b','usb-b','usb',2,'x',2))
            self.assertEqual(len(store.read('b')),1)
            with self.assertRaises(PermissionError): store.read('c')
            with self.assertRaises(PermissionError): store.append(SignalEvent('b','rf-a','rf',3,'x',1))
    def test_limit_and_type(self):
        with SignalJournal(self.path,self.policies) as store:
            for i in range(3): store.append(SignalEvent('a','rf-a','rf',i,'x',1))
            self.assertEqual(len(store.read('a',limit=2)),2)
            with self.assertRaises(ValueError): store.read('a',limit=True)
            with self.assertRaises(ValueError): store.append('bad')
    def test_reject_unknown_source(self):
        with SignalJournal(self.path,self.policies) as store:
            with self.assertRaises(PermissionError): store.append(SignalEvent('a','unknown','rf',1,'x',1))
            self.assertEqual(store.count('a'),0)
