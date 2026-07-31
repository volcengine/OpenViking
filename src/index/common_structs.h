// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include <vector>
#include <string>
#include <cstdint>

namespace vectordb {

struct AddDataRequest {
  uint64_t label = 0;
  std::vector<float> vector;
  std::vector<std::string> sparse_raw_terms;
  std::vector<float> sparse_values;

  std::string fields_str;
  std::string old_fields_str;
};

struct DeleteDataRequest {
  uint64_t label = 0;
  std::string old_fields_str;
};

struct SearchRequest {
  std::vector<float> query;
  std::vector<std::string> sparse_raw_terms;
  std::vector<float> sparse_values;
  uint32_t topk = 0;
  std::string dsl;
};

struct SearchResult {
  uint32_t result_num = 0;
  std::vector<uint64_t> labels;
  std::vector<float> scores;
  std::string extra_json;
};

// A filter bitmap projected into an external row order. Bit i in
// bitset_words corresponds to ordered_labels[i] supplied by the caller.
// This keeps native logical offsets private while allowing an external dense
// index (for example, cuVS) to reuse the native scalar-index semantics.
struct FilterResult {
  uint64_t eligible_count = 0;
  std::vector<uint32_t> bitset_words;
  uint64_t native_filter_token = 0;
};

struct StateResult {
  uint64_t update_timestamp = 0;
  uint64_t element_count = 0;
};

}  // namespace vectordb
