import unittest
from autogen_core._signal_ingest import SourcePolicy, SignalIngestor

class IngestTests(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_and_correlation(self):
        s=SignalIngestor({'sensor-a':SourcePolicy('alpha',frozenset({'rf','usb'}))},max_queue=2)
        await s.submit({'version':1,'tenant_id':'alpha','source_id':'sensor-a','domain':'rf','timestamp_ms':100,'kind':'anomaly','severity':7})
        await s.submit({'version':1,'tenant_id':'alpha','source_id':'sensor-a','domain':'usb','timestamp_ms':102,'kind':'anomaly','severity':7})
        result=await s.correlate('alpha')
        self.assertEqual(result.classification,'review')
        self.assertEqual(result.event_count,2)

    async def test_fail_closed_tenant_and_domain(self):
        s=SignalIngestor({'s':SourcePolicy('alpha',frozenset({'rf'}))})
        for event in ({'version':1,'tenant_id':'beta','source_id':'s','domain':'rf','timestamp_ms':0,'kind':'x','severity':2}, {'version':1,'tenant_id':'alpha','source_id':'s','domain':'usb','timestamp_ms':0,'kind':'x','severity':2}):
            with self.assertRaises(PermissionError): await s.submit(event)

    async def test_no_implicit_coercion_or_extra_fields(self):
        s=SignalIngestor({'s':SourcePolicy('a',frozenset({'rf'}))})
        event={'version':1,'tenant_id':'a','source_id':'s','domain':'rf','timestamp_ms':0,'kind':'x','severity':1}
        for update in ({'version':True},{'severity':'9'},{'extra':'secret'},{'timestamp_ms':-1}):
            with self.assertRaises(ValueError): await s.submit(event|update)

    async def test_queue_backpressure(self):
        s=SignalIngestor({'s':SourcePolicy('a',frozenset({'rf'}))},max_queue=1)
        e={'version':1,'tenant_id':'a','source_id':'s','domain':'rf','timestamp_ms':0,'kind':'x','severity':1}
        await s.submit(e)
        with self.assertRaises(OverflowError): await s.submit(e)
        self.assertEqual((await s.correlate('a')).event_count,1)

    async def test_empty_and_unknown_tenant(self):
        s=SignalIngestor({'s':SourcePolicy('a',frozenset({'rf'}))})
        self.assertEqual((await s.correlate('a')).event_count,0)
        with self.assertRaises(PermissionError): await s.correlate('other')

    async def test_batch_atomicity(self):
        s=SignalIngestor({'s':SourcePolicy('a',frozenset({'rf'}))})
        e={'version':1,'tenant_id':'a','source_id':'s','domain':'rf','timestamp_ms':0,'kind':'x','severity':1}
        with self.assertRaises(PermissionError): await s.submit_batch((e,e|{'tenant_id':'b'}))
        self.assertEqual((await s.correlate('a')).event_count,0)

    async def test_drain_frees_capacity_and_is_tenant_scoped(self):
        s=SignalIngestor({'s':SourcePolicy('a',frozenset({'rf'})), 't':SourcePolicy('b',frozenset({'usb'}))},max_queue=1)
        a={'version':1,'tenant_id':'a','source_id':'s','domain':'rf','timestamp_ms':0,'kind':'x','severity':1}
        b={'version':1,'tenant_id':'b','source_id':'t','domain':'usb','timestamp_ms':0,'kind':'x','severity':1}
        await s.submit_batch((a,b))
        self.assertEqual(len(await s.drain('a')),1)
        self.assertEqual((await s.correlate('a')).event_count,0)
        self.assertEqual((await s.correlate('b')).event_count,1)
        await s.submit(a)
        with self.assertRaises(PermissionError): await s.drain('unknown')

if __name__=='__main__':unittest.main()
