import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from autogen_core._pegasus_evidence import inspect_mobile_artifact

class MobileArtifactTests(unittest.TestCase):
    def test_pkpass_structure(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'ticket.pkpass'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('pass.json','{"formatVersion":1}')
            result=inspect_mobile_artifact(path)
            self.assertEqual(result.format,'zip')
            self.assertEqual(result.assessment,'structure_only')
            self.assertEqual(len(result.sha256),64)
    def test_reject_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'ticket.pkpass'
            with zipfile.ZipFile(path,'w') as z: z.writestr('../escape','bad')
            self.assertEqual(inspect_mobile_artifact(path).assessment,'unsafe_archive')
    def test_reject_bomb(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'ticket.pkpass'
            with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z: z.writestr('large',b'0'*100000)
            self.assertEqual(inspect_mobile_artifact(path,max_uncompressed=1000).assessment,'unsafe_archive')
    def test_reject_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'link.pkpass'
            (Path(d)/'actual.pkpass').write_bytes(b'data')
            path.symlink_to(Path(d)/'actual.pkpass')
            with self.assertRaises(ValueError): inspect_mobile_artifact(path)
    def test_size_limit(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'sample.gif'
            path.write_bytes(b'GIF89a'+b'x'*100)
            with self.assertRaises(ValueError): inspect_mobile_artifact(path,max_bytes=32)
    def test_gif_not_infection_proof(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'sample.gif'
            path.write_bytes(b'GIF89a'+b'x'*20)
            result=inspect_mobile_artifact(path)
            self.assertEqual(result.format,'gif')
            self.assertEqual(result.assessment,'structure_only')
    def test_invalid_signature(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'fake.gif'
            path.write_bytes(b'NOTGIF')
            self.assertEqual(inspect_mobile_artifact(path).assessment,'unknown_format')
if __name__=='__main__': unittest.main()
