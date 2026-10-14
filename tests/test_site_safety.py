"""No test probe may navigate to the production site."""

import unittest

from tools.site_safety import require_nonproduction_https


class SiteSafetyTests(unittest.TestCase):
    def test_rejects_production(self):
        for url in (
            "https://www.pychess.org/analysis/chess",
            "https://pychess.org/",
            "https://dev.pychess.org/",
            "https://www.pychess.org.evil.example@pychess.org/",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                require_nonproduction_https(url)

    def test_allows_nonproduction(self):
        for url in ("https://probe.pychess.invalid/analysis/chess", "https://example.com/"):
            with self.subTest(url=url):
                self.assertEqual(require_nonproduction_https(url), url)

    def test_rejects_non_https(self):
        with self.assertRaises(ValueError):
            require_nonproduction_https("http://localhost:8000")
