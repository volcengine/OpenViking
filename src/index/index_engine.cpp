// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include "index_engine.h"
#include "index/detail/index_manager_impl.h"
#include "index/detail/fields_dict.h"
#include <stdexcept>
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

std::optional<SearchResult> IndexEngine::search_with_filter_token(
    const SearchRequest& req, uint64_t filter_token) {
  SearchResult result;
  bool token_found = false;
  const int ret =
      impl_->search_with_filter_token(req, filter_token, result, token_found);
  if (ret != 0) {
    throw std::runtime_error("Failed to search with native filter token");
  }
  if (!token_found) {
    return std::nullopt;
  }
  result.result_num = result.labels.size();
  return result;
}

int IndexEngine::set_filter_layout(
    const std::vector<uint64_t>& ordered_labels) {
  return impl_->set_filter_layout(ordered_labels);
}

FilterResult IndexEngine::evaluate_filter(
    const std::string& dsl, uint64_t max_cached_candidates) {
  FilterResult result;
  const int ret =
      impl_->evaluate_filter(dsl, max_cached_candidates, result);
  if (ret != 0) {
    throw std::runtime_error("Failed to evaluate native scalar filter");
  }
  return result;
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
