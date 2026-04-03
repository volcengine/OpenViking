// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include <iostream>
#include <vector>
#include <string>
#include <algorithm>
#include <cassert>
#include <cmath>

#include "common/string_utils.h"
#include "common/zip_sort.h"
#include "common/log_utils.h"
#include "spdlog/spdlog.h"

// Simple Assertion Macro
#define ASSERT_EQ(a, b) \
  if ((a) != (b)) { \
    SPDLOG_ERROR("Assertion failed: {} != {} ({} != {}) at {}:{}", #a, #b, (a), (b), __FILE__, __LINE__); \
    std::exit(1); \
  }

#define ASSERT_TRUE(a) \
  if (!(a)) { \
    SPDLOG_ERROR("Assertion failed: {} at {}:{}", #a, __FILE__, __LINE__); \
    std::exit(1); \
  }

using namespace vectordb;

void test_string_split() {
    SPDLOG_INFO("[Running] test_string_split");
    std::vector<std::string> tokens;
    
    // Case 1: Normal split
    split(tokens, "apple,banana,orange", ",");
    ASSERT_EQ(tokens.size(), 3);
    ASSERT_EQ(tokens[0], "apple");
    ASSERT_EQ(tokens[1], "banana");
    ASSERT_EQ(tokens[2], "orange");
    tokens.clear();

    // Case 2: Empty string
    split(tokens, "", ",");
    ASSERT_EQ(tokens.size(), 0);
    tokens.clear();

    // Case 3: No delimiter
    split(tokens, "helloworld", ",");
    ASSERT_EQ(tokens.size(), 1);
    ASSERT_EQ(tokens[0], "helloworld");
    tokens.clear();

    // Case 4: Continuous delimiters
    // According to split impl: if (end > start) tokens.push_back(...)
    // This means empty strings between continuous delimiters are skipped.
    split(tokens, "a,,b", ",");
    ASSERT_EQ(tokens.size(), 2);
    ASSERT_EQ(tokens[0], "a");
    ASSERT_EQ(tokens[1], "b");
    tokens.clear();

    SPDLOG_INFO("[Passed] test_string_split");
}

void test_string_format() {
    SPDLOG_INFO("[Running] test_string_format");
    
    // Case 1: Basic format
    std::string s1 = sformat("Hello {}", "World");
    ASSERT_EQ(s1, "Hello World");

    // Case 2: Multiple arguments
    std::string s2 = sformat("{} + {} = {}", 1, 2, 3);
    ASSERT_EQ(s2, "1 + 2 = 3");

    // Case 3: Float number (Check prefix only due to to_string precision)
    std::string s3 = sformat("Value: {}", 3.14);
    ASSERT_TRUE(s3.find("Value: 3.14") == 0);

    // Case 4: Fewer arguments than placeholders (Should preserve {})
    std::string s4 = sformat("{} {}", "OnlyOne");
    ASSERT_EQ(s4, "OnlyOne {}");

    // Case 5: More arguments than placeholders (Extra args ignored)
    std::string s5 = sformat("{}", "First", "Second");
    ASSERT_EQ(s5, "First");

    SPDLOG_INFO("[Passed] test_string_format");
}

void test_zip_sort() {
    SPDLOG_INFO("[Running] test_zip_sort");

    // Case 1: Basic sort
    std::vector<int> keys = {3, 1, 4, 2};
    std::vector<std::string> values = {"C", "A", "D", "B"};
    
    // Sort ascending by key
    ZipSortBranchOptimized(
        [](int a, int b) { return a < b; },
        keys.begin(), keys.end(),
        values.begin(), values.end()
    );

    ASSERT_EQ(keys[0], 1); ASSERT_EQ(values[0], "A");
    ASSERT_EQ(keys[1], 2); ASSERT_EQ(values[1], "B");
    ASSERT_EQ(keys[2], 3); ASSERT_EQ(values[2], "C");
    ASSERT_EQ(keys[3], 4); ASSERT_EQ(values[3], "D");

    // Case 2: Empty array
    std::vector<int> empty_keys;
    std::vector<int> empty_vals;
    ZipSortBranchOptimized(
        [](int a, int b) { return a < b; },
        empty_keys.begin(), empty_keys.end(),
        empty_vals.begin(), empty_vals.end()
    );
    ASSERT_TRUE(empty_keys.empty());

    // Case 3: Single element
    std::vector<int> single_key = {1};
    std::vector<int> single_val = {100};
    ZipSortBranchOptimized(
        [](int a, int b) { return a < b; },
        single_key.begin(), single_key.end(),
        single_val.begin(), single_val.end()
    );
    ASSERT_EQ(single_key[0], 1);
    ASSERT_EQ(single_val[0], 100);

    SPDLOG_INFO("[Passed] test_zip_sort");
}

int main() {
    init_logging("INFO", "stdout", "[%Y-%m-%d %H:%M:%S.%e] [%l] %v");
    SPDLOG_INFO("Starting Common Tests...");
    test_string_split();
    test_string_format();
    test_zip_sort();
    SPDLOG_INFO("All Common Tests Passed!");
    return 0;
}
