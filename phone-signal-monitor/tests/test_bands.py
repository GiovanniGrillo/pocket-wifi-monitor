import unittest

from bands import band_label, classify_signal, decode_earfcn


class BandsTests(unittest.TestCase):
    def test_decode_known_low_band(self):
        self.assertEqual(decode_earfcn(6300), (6300, 20, "B20 (800)", True))

    def test_decode_unknown_earfcn_keeps_value(self):
        self.assertEqual(decode_earfcn(5000), (5000, None, "", None))

    def test_direct_band_label(self):
        self.assertEqual(band_label(3), ("B3 (1800)", False))
        self.assertEqual(band_label(999), ("B999", None))

    def test_signal_quality_is_limited_by_sinr(self):
        self.assertEqual(classify_signal(-85, -1), "poor")
        self.assertEqual(classify_signal(-85, 10), "good")


if __name__ == "__main__":
    unittest.main()
