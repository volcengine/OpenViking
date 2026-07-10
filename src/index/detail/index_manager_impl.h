// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include "index/index_manager.h"
#include "index/common_structs.h"
#include "index/detail/meta/manager_meta.h"
#include "common/json_utils.h"
#include "index/detail/scalar/scalar_index.h"
#include "index/detail/vector/vector_index_adapter.h"
#include "index/detail/search_context.h"
#include "index/detail/scalar/bitmap_holder/bitmap.h"

#include <shared_mutex>
#include <deque>
#include <filesystem>
#include <memory>
#include <mutex>
#include <stdio.h>
#include <unordered_map>

namespace vectordb {

class IndexManagerImpl : public IndexManager {
 public:
  IndexManagerImpl(const std::string& path_or_json);

  ~IndexManagerImpl() {
    scalar_index_.reset();
    vector_index_.reset();
    manager_meta_.reset();
  }

  int search(const SearchRequest& req, SearchResult& result) override;

  int search_with_filter_token(const SearchRequest& req,
                               uint64_t filter_token,
                               SearchResult& result,
                               bool& token_found) override;

  int set_filter_layout(
      const std::vector<uint64_t>& ordered_labels) override;

  int evaluate_filter(const std::string& dsl,
                      uint64_t max_cached_candidates,
                      FilterResult& result) override;

  int add_data(const std::vector<AddDataRequest>& data_list) override;

  int delete_data(const std::vector<DeleteDataRequest>& data_list) override;

  int64_t dump(const std::string& dir) override;

  int get_state(StateResult& state_result) override;

 private:
  void init_from_json(const JsonDoc& json);

  void load_from_path(const std::filesystem::path& dir);

  // Helper functions for search
  BitmapPtr calculate_filter_bitmap(const SearchContext& ctx,
                                    const std::string& dsl);

  int handle_sorter_query(const SearchContext& ctx, const BitmapPtr& bitmap,
                          SearchResult& result, const std::string& dsl);

  int perform_vector_recall(const SearchRequest& req, SearchContext& ctx,
                            const BitmapPtr& bitmap, SearchResult& result);

  void register_label_offset_converter_();

  uint64_t cache_filter_bitmap_(const BitmapPtr& bitmap);

  void clear_filter_token_cache_();

 private:
  std::shared_mutex rw_mutex_;
  std::shared_ptr<ManagerMeta> manager_meta_;
  std::shared_ptr<ScalarIndex> scalar_index_;
  std::shared_ptr<VectorIndexAdapter> vector_index_;
  std::vector<uint32_t> filter_layout_offsets_;
  std::mutex filter_token_mutex_;
  uint64_t next_filter_token_ = 1;
  std::deque<uint64_t> filter_token_order_;
  std::unordered_map<uint64_t, BitmapPtr> filter_token_cache_;
};

}  // namespace vectordb
