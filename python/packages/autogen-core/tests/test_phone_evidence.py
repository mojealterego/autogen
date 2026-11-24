import unittest
from autogen_core._phone_evidence import inspect_e164

class PhoneEvidenceTests(unittest.TestCase):
    def test_valid(self):
        result = inspect_e164('+447911123456')
        self.assertTrue(result.valid)
        self.assertIsNone(result.country_code)
        self.assertEqual(result.reason, 'syntax_only_no_carrier_or_location')
        self.assertEqual(len(result.digest), 64)
    def test_reject_missing_plus(self):
        self.assertFalse(inspect_e164('447911123456').valid)
    def test_reject_leading_zero(self):
        self.assertFalse(inspect_e164('+01234').valid)
    def test_reject_overlength(self):
        self.assertFalse(inspect_e164('+'+'1'*16).valid)
    def test_reject_unicode_digits(self):
        self.assertFalse(inspect_e164('+٤٤٧٩١١').valid)
    def test_reject_non_string(self):
        self.assertFalse(inspect_e164(None).valid)
    def test_reject_injection(self):
        self.assertFalse(inspect_e164('+447911123456\nDROP').valid)
if __name__ == '__main__': unittest.main()
