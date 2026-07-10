// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <string>
#include <vector>
#include "index/common_structs.h"

namespace vectordb {
class IndexManager {
 public:
  IndexManager() = default;

  virtual ~IndexManager() = default;

  virtual int search(const SearchRequest& req, SearchResult& result) = 0;

  virtual int search_with_filter_token(const SearchRequest& req,
                                       uint64_t filter_token,
                                       SearchResult& result,
                                       bool& token_found) = 0;

  virtual int set_filter_layout(
      const std::vector<uint64_t>& ordered_labels) = 0;

  virtual int evaluate_filter(const std::string& dsl,
                              uint64_t max_cached_candidates,
                              FilterResult& result) = 0;

  virtual int add_data(const std::vector<AddDataRequest>& data_list) = 0;

  virtual int delete_data(const std::vector<DeleteDataRequest>& data_list) = 0;

  virtual int64_t dump(const std::string& dir) = 0;

  virtual int get_state(StateResult& state_result) = 0;
};
}  // namespace vectordb
