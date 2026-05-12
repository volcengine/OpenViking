// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include <string>
#include <vector>
#include <memory>

#include "index/common_structs.h"
#include "index/index_manager.h"

namespace vectordb {

class IndexEngine {
 public:
  IndexEngine(const std::string& path_or_json);

  bool is_valid() const {
    return impl_ != nullptr;
  }
  int add_data(const std::vector<AddDataRequest>& data_list);

  int delete_data(const std::vector<DeleteDataRequest>& data_list);

  SearchResult search(const SearchRequest& req);

  std::vector<SearchResult> search_batch(const std::vector<SearchRequest>& reqs);

  int64_t dump(const std::string& dir);

  StateResult get_state();

 private:
  std::shared_ptr<IndexManager> impl_ = nullptr;
};

}  // namespace vectordb