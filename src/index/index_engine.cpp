// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include "index_engine.h"
#include "index/detail/index_manager_impl.h"
#include "index/detail/fields_dict.h"
#include <unistd.h>

namespace vectordb {
IndexEngine::IndexEngine(const std::string& path_or_json) {
  impl_ = std::make_shared<IndexManagerImpl>(path_or_json);
}

SearchResult IndexEngine::search(const SearchRequest& req) {
  SearchResult result;
  impl_->search(req, result);
  result.result_num = result.labels.size();
  return result;
}

std::vector<SearchResult> IndexEngine::search_batch(
    const std::vector<SearchRequest>& reqs) {
  std::vector<SearchResult> results;
  impl_->search_batch(reqs, results);
  return results;
}

int IndexEngine::add_data(const std::vector<AddDataRequest>& data_list) {
  return impl_->add_data(data_list);
}

int IndexEngine::delete_data(const std::vector<DeleteDataRequest>& data_list) {
  return impl_->delete_data(data_list);
}

int64_t IndexEngine::dump(const std::string& dir) {
  return impl_->dump(dir);
}

StateResult IndexEngine::get_state() {
  StateResult state_result;
  impl_->get_state(state_result);
  return state_result;
}

}  // namespace vectordb