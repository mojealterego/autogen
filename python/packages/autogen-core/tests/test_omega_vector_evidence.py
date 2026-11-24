import unittest
from autogen_core._omega_vector_evidence import VectorEvidence, QdrantEvidenceStore

class FakeQdrant:
    def __init__(self): self.calls=[]
    def upsert(self, **kwargs): self.calls.append(kwargs)

class VectorTests(unittest.TestCase):
    def test_scoped_payload(self):
        client=FakeQdrant()
        store=QdrantEvidenceStore(client,collection="evidence")
        store.upsert(VectorEvidence("tenant","case","item",("a"*64), (0.5,0.25)))
        payload=client.calls[0]["points"][0]["payload"]
        self.assertEqual((payload["tenant_id"],payload["case_id"]),("tenant","case"))
        self.assertNotIn("raw_text",payload)
    def test_invalid_vector(self):
        with self.assertRaises(ValueError): VectorEvidence("t","c","i","a"*64,(float("nan"),))
    def test_empty_vector(self):
        with self.assertRaises(ValueError): VectorEvidence("t","c","i","a"*64,())
    def test_reject_identifier(self):
        with self.assertRaises(ValueError): VectorEvidence("../t","c","i","a"*64,(1.0,))
    def test_invalid_digest(self):
        with self.assertRaises(ValueError): VectorEvidence("t","c","i","bad",(1.0,))
if __name__=="__main__": unittest.main()
