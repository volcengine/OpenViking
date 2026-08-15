# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import unittest

from openviking.storage.vectordb.utils.str_to_uint64 import str_to_uint64


class TestStrToUint64(unittest.TestCase):
    def test_returns_uint64_within_range(self):
        for sample in ("", "a", "00000000-0000-0000-0000-000000000000", "viking://resources/README"):
            value = str_to_uint64(sample)
            self.assertIsInstance(value, int)
            self.assertTrue(0 <= value < (1 << 64), f"value out of uint64 range: {value}")

    def test_deterministic(self):
        sample = "viking://resources/README/.abstract.md"
        self.assertEqual(str_to_uint64(sample), str_to_uint64(sample))

    def test_accepts_non_ascii_strings(self):
        # Regression test: str was previously passed straight to xxhash, which
        # raised ``TypeError: Strings must be encoded before hashing`` and
        # caused every partial-update upsert to be skipped silently.
        for sample in ("中文记忆", "混合 viking://资源/路径", "\U0001f600emoji"):
            value = str_to_uint64(sample)
            self.assertIsInstance(value, int)
            self.assertTrue(0 <= value < (1 << 64))

    def test_distinct_inputs_have_distinct_values(self):
        a = str_to_uint64("sparse_raw_terms")
        b = str_to_uint64("sparse_values")
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
