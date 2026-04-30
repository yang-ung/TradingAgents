import unittest
from unittest.mock import patch

import pytest

from cli.utils import normalize_ticker_symbol
from tradingagents.agents.utils.agent_utils import build_instrument_context


@pytest.mark.unit
class TickerSymbolHandlingTests(unittest.TestCase):
    def test_normalize_ticker_symbol_preserves_exchange_suffix(self):
        self.assertEqual(normalize_ticker_symbol(" cnc.to "), "CNC.TO")

    def test_normalize_ticker_symbol_auto_detects_kospi_code(self):
        with patch("tradingagents.ticker_utils._lookup_krx_market", return_value="코스피"):
            self.assertEqual(normalize_ticker_symbol(" 005930 "), "005930.KS")

    def test_normalize_ticker_symbol_falls_back_to_kosdaq_code(self):
        with patch("tradingagents.ticker_utils._lookup_krx_market", return_value="코스닥"):
            self.assertEqual(normalize_ticker_symbol("035760"), "035760.KQ")

    def test_build_instrument_context_mentions_exact_symbol(self):
        context = build_instrument_context("7203.T")
        self.assertIn("7203.T", context)
        self.assertIn("exchange suffix", context)

    def test_build_instrument_context_mentions_korean_suffixes(self):
        context = build_instrument_context("005930.KS")
        self.assertIn(".KS", context)
        self.assertIn(".KQ", context)


if __name__ == "__main__":
    unittest.main()
