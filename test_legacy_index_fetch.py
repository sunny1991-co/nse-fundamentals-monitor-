"""NSE historical index metadata retrieval must stop on access denial."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import nse_legacy_index_fetch as index


class IndexRetrievalTests(unittest.TestCase):
    def test_http_403_preserves_source_and_skips_repeated_access(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            response = requests.Response()
            response.status_code = 403
            error = requests.HTTPError("403 Forbidden", response=response)
            with patch.object(index.time, "sleep", return_value=None):
                states = index.collect(root, session_factory=lambda: object(), fetch=Mock(side_effect=error))
            self.assertEqual(len(states), 2)
            self.assertEqual(states[0]["Index_Status"], "OFFICIAL_NSE_ACCESS_BLOCKED")
            self.assertEqual(states[1]["Index_Status"], "NOT_ATTEMPTED_AFTER_403_429")
            self.assertFalse((root / "nse_legacy_financial_results_2022-23.csv").exists())

    def test_legacy_archive_index_stays_unverified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = {
                "symbol": "TESTABC", "companyName": "Test Ltd",
                "toDate": "31-Mar-2023", "audited": "Audited",
                "consolidated": "Consolidated", "period": "Annual",
                "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/TEST_2023.xml",
            }
            with patch.object(index.time, "sleep", return_value=None):
                states = index.collect(
                    root,
                    session_factory=lambda: object(),
                    fetch=lambda _s, _a, _b: [fixture],
                )
            self.assertEqual(len(states), 2)
            self.assertTrue(all(x["Index_Status"] == "INDEX_METADATA_COLLECTED_NOT_VERIFIED" for x in states))
            path = root / "nse_legacy_financial_results_2022-23.csv"
            with path.open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["Symbol"], "TESTABC")
            self.assertEqual(rows[0]["Period"], "Annual")
            self.assertNotIn("NSE_Consolidation_Verified", rows[0])

    def test_empty_and_unknown_structures_never_write_source_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(index.time, "sleep", return_value=None):
                states = index.collect(root, session_factory=lambda: object(), fetch=lambda *_: [])
            self.assertTrue(all(x["Index_Status"] == "EMPTY_OFFICIAL_API_RESULT" for x in states))
            self.assertFalse((root / "nse_legacy_financial_results_2023-24.csv").exists())
        with self.assertRaises(ValueError):
            index.list_rows({"unauthorized": True})


if __name__ == "__main__":
    unittest.main()
