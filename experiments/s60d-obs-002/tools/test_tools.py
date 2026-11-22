import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from access_summary import summarize
from cpu_sample import accumulate, parse_stat, snapshot


class ToolsTest(unittest.TestCase):
    def test_log(self):
        result = summarize(['127.0.0.1 - - [x] "GET /a?secret=1 HTTP/1.1" 200 1\n',
                            '2026/01/01 00:00:00 [warn] request: "GET /a HTTP/1.1"\n', 'bad\n'])
        self.assertEqual((result['total'], result['access'], result['warn'], result['malformed_or_unclassified']), (3, 1, 1, 1))
        self.assertEqual(result['paths'], {'/a': 1})

    def test_stat_and_ticks(self):
        fields = ['S'] + ['0'] * 19
        fields[11:15] = ['100', '50', '9999', '9999']
        fields[19] = '42'
        self.assertEqual(parse_stat('1 (a ) b) ' + ' '.join(fields)), (100, 50, 42))
        processes, errors = {}, Counter()
        accumulate({(1, 42): (100, 'a'), (2, 1): (10, 'x')},
                   {(1, 42): (150, 'b'), (2, 2): (100, 'x')}, 2, 100, processes, errors)
        self.assertEqual(processes[(1, 42)]['seconds'], 0.5)
        self.assertNotIn((2, 2), processes)
        accumulate({(1, 42): (150, 'b')}, {(1, 42): (149, 'b')}, 1, 100, processes, errors)
        self.assertEqual(errors['negative_delta'], 1)

    def test_read_failures(self):
        with tempfile.TemporaryDirectory() as root:
            proc = Path(root)
            (proc / '1').mkdir()
            (proc / '2').mkdir()
            (proc / '2' / 'stat').write_text('invalid')
            (proc / '2' / 'comm').write_text('x')
            errors = Counter()
            self.assertEqual(snapshot(proc, errors), {})
            self.assertEqual(errors['vanished'], 1)
            self.assertEqual(errors['parse_failure'], 1)
            with patch.object(Path, 'iterdir', side_effect=PermissionError):
                snapshot(proc, errors)
            self.assertEqual(errors['enumeration_failure'], 1)


if __name__ == '__main__':
    unittest.main()
