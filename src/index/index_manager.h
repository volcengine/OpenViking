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

  // Batch search: process multiple queries sharing the same filter.
  // Default implementation loops over single-query search.
  virtual int search_batch(const std::vector<SearchRequest>& reqs,
                           std::vector<SearchResult>& results) {
    results.resize(reqs.size());
    for (size_t i = 0; i < reqs.size(); ++i) {
      int ret = search(reqs[i], results[i]);
      if (ret != 0) return ret;
    }
    return 0;
  }

  virtual int add_data(const std::vector<AddDataRequest>& data_list) = 0;

  virtual int delete_data(const std::vector<DeleteDataRequest>& data_list) = 0;

  virtual int64_t dump(const std::string& dir) = 0;

  virtual int get_state(StateResult& state_result) = 0;
};
}  // namespace vectordb