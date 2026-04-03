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

  virtual int add_data(const std::vector<AddDataRequest>& data_list) = 0;

  virtual int delete_data(const std::vector<DeleteDataRequest>& data_list) = 0;

  virtual int64_t dump(const std::string& dir) = 0;

  virtual int get_state(StateResult& state_result) = 0;
};
}  // namespace vectordb