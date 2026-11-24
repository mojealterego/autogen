"""Neo4j evidence adapter tests with a fake session, no network."""
import unittest
from autogen_core._omega_graph_evidence import EvidenceEdge, Neo4jEvidenceGraph

class FakeSession:
    def __init__(self): self.calls=[]
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def execute_write(self, callback): return callback(self)
    def run(self, query, **kwargs):
        self.calls.append((query,kwargs))
        return None

class FakeDriver:
    def __init__(self): self.session_instance=FakeSession(); self.database=None
    def session(self, *, database):
        self.database=database
        return self.session_instance

class GraphTests(unittest.TestCase):
    def setUp(self):
        self.driver=FakeDriver()
        self.graph=Neo4jEvidenceGraph(self.driver, database="neo4j")
    def test_parameterized_edge(self):
        edge=EvidenceEdge("tenant","case","evidence-A","related_to","evidence-B","a"*64)
        self.graph.add_edge(edge)
        query, params=self.driver.session_instance.calls[0]
        self.assertIn("$tenant_id",query)
        self.assertNotIn("evidence-A",query)
        self.assertEqual(params["source_id"],"evidence-A")
    def test_tenant_case_in_both_nodes(self):
        self.graph.add_edge(EvidenceEdge("tenant","case","A","related_to","B","a"*64))
        query,_=self.driver.session_instance.calls[0]
        self.assertGreaterEqual(query.count("tenant_id:"),2)
        self.assertGreaterEqual(query.count("case_id:"),2)
    def test_reject_relation_injection(self):
        with self.assertRaises(ValueError):
            EvidenceEdge("tenant","case","A","related_to` MATCH (n) DETACH DELETE n","B","a"*64)
    def test_reject_cross_case(self):
        with self.assertRaises(ValueError):
            EvidenceEdge("tenant","../case","A","related_to","B","a"*64)
    def test_reject_untrusted_digest(self):
        with self.assertRaises(ValueError):
            EvidenceEdge("tenant","case","A","related_to","B","no")
    def test_reject_invalid_driver(self):
        with self.assertRaises(TypeError):
            Neo4jEvidenceGraph(None)
if __name__=="__main__": unittest.main()
