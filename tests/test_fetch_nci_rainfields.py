"""Acquisition integrity checks using synthetic archives, without network access."""
import importlib.util
from datetime import date
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('fetch', Path(__file__).resolve().parents[1] / 'scripts/fetch_nci_rainfields.py')
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)


class AcquisitionTests(unittest.TestCase):
    def test_irregular_timestamp_is_not_complete_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'day.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                for i in range(288):
                    hh, mm = divmod(i*5, 60)
                    stamp = '011900' if i == 15 else f'{hh:02}{mm:02}00'
                    z.writestr(f'71_20240101_{stamp}.prcp-crate.nc', b'CDF\x01test')
            r = fetch.extract(archive, root / 'out', 71, date(2024, 1, 1))
            self.assertEqual(r['missing_5min_utc'], ['2024-01-01T01:15:00Z'])
            self.assertEqual(r['off_grid_utc'], ['2024-01-01T01:19:00Z'])
            r['archive_sha256'] = fetch.digest(archive)
            self.assertTrue(fetch.verified(r, archive, root / 'out'))
            (root / 'out' / r['files'][0]['name']).write_bytes(b'corrupted')
            self.assertFalse(fetch.verified(r, archive, root / 'out'))

    def test_unsafe_member_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with zipfile.ZipFile(root / 'day.zip', 'w') as z:
                z.writestr('../escape.nc', b'CDF\x01test')
            with self.assertRaises(ValueError):
                fetch.extract(root / 'day.zip', root / 'out', 71, date(2024, 1, 1))
            self.assertFalse((root / 'out').exists())

    def test_html_response_is_not_accepted_as_netcdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with zipfile.ZipFile(root / 'day.zip', 'w') as z:
                z.writestr('71_20240101_000000.prcp-crate.nc', b'<html>failure</html>')
            with self.assertRaises(ValueError):
                fetch.extract(root / 'day.zip', root / 'out', 71, date(2024, 1, 1))
            self.assertEqual(list((root / 'out').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
