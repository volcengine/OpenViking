// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include <iostream>
#include <vector>
#include <string>
#include <algorithm>
#include <cassert>
#include <cmath>
#include <chrono>
#include <filesystem>
#include <fstream>
#include "rapidjson/document.h"

#include "common/string_utils.h"
#include "common/zip_sort.h"
#include "common/log_utils.h"
#include "spdlog/spdlog.h"

// Simple Assertion Macro
#define ASSERT_EQ(a, b)                                                    \
  if ((a) != (b)) {                                                        \
    SPDLOG_ERROR("Assertion failed: {} != {} ({} != {}) at {}:{}", #a, #b, \
                 (a), (b), __FILE__, __LINE__);                            \
    std::exit(1);                                                          \
  }

#define ASSERT_TRUE(a)                                                     \
  if (!(a)) {                                                              \
    SPDLOG_ERROR("Assertion failed: {} at {}:{}", #a, __FILE__, __LINE__); \
    std::exit(1);                                                          \
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

void test_zip_sort() {
  SPDLOG_INFO("[Running] test_zip_sort");

  // Case 1: Basic sort
  std::vector<int> keys = {3, 1, 4, 2};
  std::vector<std::string> values = {"C", "A", "D", "B"};

  // Sort ascending by key
  ZipSortBranchOptimized([](int a, int b) { return a < b; }, keys.begin(),
                         keys.end(), values.begin(), values.end());

  ASSERT_EQ(keys[0], 1);
  ASSERT_EQ(values[0], "A");
  ASSERT_EQ(keys[1], 2);
  ASSERT_EQ(values[1], "B");
  ASSERT_EQ(keys[2], 3);
  ASSERT_EQ(values[2], "C");
  ASSERT_EQ(keys[3], 4);
  ASSERT_EQ(values[3], "D");

  // Case 2: Empty array
  std::vector<int> empty_keys;
  std::vector<int> empty_vals;
  ZipSortBranchOptimized([](int a, int b) { return a < b; }, empty_keys.begin(),
                         empty_keys.end(), empty_vals.begin(),
                         empty_vals.end());
  ASSERT_TRUE(empty_keys.empty());

  // Case 3: Single element
  std::vector<int> single_key = {1};
  std::vector<int> single_val = {100};
  ZipSortBranchOptimized([](int a, int b) { return a < b; }, single_key.begin(),
                         single_key.end(), single_val.begin(),
                         single_val.end());
  ASSERT_EQ(single_key[0], 1);
  ASSERT_EQ(single_val[0], 100);

  SPDLOG_INFO("[Passed] test_zip_sort");
}

void test_logging_utf8_and_flush() {
  const auto path = std::filesystem::temp_directory_path() /
      ("ov-log-test-" + std::to_string(spdlog::details::os::pid()) + "-" +
       std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
  const std::string common = "%(asctime)s %(levelname)s %(name)s pid=%(process)d request_id=%(request_id)s trace_id=%(trace_id)s message=%(message)s";
  init_logging("INFO", path.string(), common);
  ASSERT_TRUE(spdlog::default_logger()->flush_level() == spdlog::level::err);
  std::vector<std::pair<std::string, std::string>> cases = {
      {u8"\u4e2d\u6587 \u00e9 \U0001f600", u8"\u4e2d\u6587 \u00e9 \U0001f600"},
      {"line\nquote\"slash\\", "line\nquote\"slash\\"},
      {std::string("zero\0byte", 9), std::string("zero\0byte", 9)},
      {std::string("invalid\xff", 8), u8"invalid\ufffd"},
      {std::string("\xc0\xaf", 2), u8"\ufffd\ufffd"},
      {std::string("\xed\xa0\x80", 3), u8"\ufffd\ufffd\ufffd"},
      {std::string("\xf4\x90\x80\x80", 4), u8"\ufffd\ufffd\ufffd\ufffd"},
      {std::string("\xe4\xb8", 2), u8"\ufffd\ufffd"},
      {u8"\u0080\u07ff\u0800\ud7ff\ue000\uffff\U00010000\U0010ffff",
       u8"\u0080\u07ff\u0800\ud7ff\ue000\uffff\U00010000\U0010ffff"},
  };
  for (int byte = 0; byte < 256; ++byte) {
    const std::string input(1, static_cast<char>(byte));
    cases.emplace_back(input, byte < 128 ? input : u8"\ufffd");
  }
  cases.emplace_back(std::string(2047, 'a') + u8"\u4e2d", std::string(2047, 'a'));
  for (const auto& entry : cases) {
    spdlog::info("{}", entry.first);
  }
  spdlog::default_logger()->flush();
  std::ifstream stream(path, std::ios::binary);
  std::string line;
  for (const auto& entry : cases) {
    ASSERT_TRUE(static_cast<bool>(std::getline(stream, line)));
    ASSERT_TRUE(line.find(" INFO vikingdb pid=") != std::string::npos);
    const auto start = line.find("message=");
    ASSERT_TRUE(start != std::string::npos);
    rapidjson::Document message;
    message.Parse<rapidjson::kParseValidateEncodingFlag | rapidjson::kParseStopWhenDoneFlag>(
        line.c_str() + start + 8);
    ASSERT_TRUE(!message.HasParseError() && message.IsString());
    ASSERT_TRUE(std::string(message.GetString(), message.GetStringLength()) == entry.second);
  }
  ASSERT_TRUE(line.find("truncated=true original_length=2050") != std::string::npos);
  ASSERT_TRUE(!std::getline(stream, line));
  stream.close();
  init_logging("INFO", "stdout", common);
  std::filesystem::remove(path);
  SPDLOG_INFO("[Passed] test_logging_utf8_and_flush");
}

int main() {
  init_logging("INFO", "stdout", "%(asctime)s %(levelname)s %(name)s pid=%(process)d request_id=%(request_id)s trace_id=%(trace_id)s message=%(message)s");
  SPDLOG_INFO("Starting Common Tests...");
  test_string_split();
  test_zip_sort();
  test_logging_utf8_and_flush();
  SPDLOG_INFO("All Common Tests Passed!");
  return 0;
}
