import importlib.util
import pathlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('analyze_benchmark', pathlib.Path(__file__).parents[1] / 'analyze-benchmark.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AnalyzeTest(unittest.TestCase):
    def test_correlates_and_normalizes_without_duplicating_access_records(self):
        access = '''127.0.0.1 [date] "GET /posts/123?x=1 HTTP/1.1" 200 request_time=0.100 upstream_time=0.090 request_id=first
127.0.0.1 [date] "GET /posts/456 HTTP/1.1" 404 request_time=0.200 upstream_time=0.190 request_id=second
127.0.0.1 - - [date] "GET /posts/123 HTTP/1.1" 200 12 "-" "benchmarker" "-"
'''
        traces = '''trace request_id="first" kind=sql_select duration_ms=4.000 error=false detail="SELECT * FROM users WHERE id IN (?, ?)"
trace request_id="first" kind=sql_select duration_ms=6.000 error=true detail="SELECT * FROM users WHERE id IN (?, ?, ?)"
trace request_id="missing" kind=file_read duration_ms=1.000 error=false detail="upload"
'''
        with tempfile.TemporaryDirectory() as tmp:
            a, t = pathlib.Path(tmp) / 'access', pathlib.Path(tmp) / 'trace'
            a.write_text(access)
            t.write_text(traces)
            report = module.analyze(a, t)
        self.assertEqual(report['coverage']['access_records'], 2)
        self.assertEqual(report['coverage']['ignored_access_lines'], 1)
        self.assertEqual(report['coverage']['unmatched_trace_events'], 1)
        route = report['routes'][0]
        self.assertEqual(route['route'], 'GET /posts/:id')
        self.assertEqual(route['statuses'], {'200': 1, '404': 1})
        self.assertEqual(route['p95_ms'], 200)
        self.assertEqual(route['operations'][0]['count'], 2)
        self.assertEqual(route['operations'][0]['errors'], 1)
        self.assertEqual(route['operations'][0]['per_request'], 1)
        self.assertEqual(route['median_request']['operations'][0]['per_request'], 2)

    def test_rejects_empty_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, t = pathlib.Path(tmp) / 'access', pathlib.Path(tmp) / 'trace'
            a.write_text('')
            t.write_text('')
            with self.assertRaises(ValueError):
                module.analyze(a, t)


if __name__ == '__main__':
    unittest.main()
