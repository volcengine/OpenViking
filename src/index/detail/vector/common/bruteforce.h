// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <vector>
#include <string>
#include <fstream>
#include <unordered_map>
#include <filesystem>
#include <algorithm>
#include <memory>
#include <cstring>
#include <stdexcept>

#include "index/detail/vector/common/vector_base.h"
#include "index/detail/meta/bruteforce_meta.h"
#include "index/detail/search_context.h"
#include "index/detail/vector/sparse_retrieval/sparse_data_holder.h"
#include "index/detail/vector/common/quantizer.h"
#include "index/detail/vector/common/space_int8.h"
#include "index/detail/vector/common/space_l2.h"
#include "index/detail/vector/common/space_ip.h"
#include "index/detail/scalar/bitmap_holder/bitmap.h"
#include "spdlog/spdlog.h"

namespace vectordb {

const std::string kFlatIndexFileName = "index_flat.data";

class BruteforceSearch {
 public:
  explicit BruteforceSearch(std::shared_ptr<BruteForceMeta> meta)
      : meta_(meta) {
    capacity_ = std::max<size_t>(1, meta_->max_element_count);

    setup_metric();
    quantizer_ = createQuantizer(meta_->quantization_type, meta_->distance_type,
                                 meta_->dimension);

    vector_byte_size_ = quantizer_->get_encoded_size();
    element_byte_size_ =
        vector_byte_size_ + sizeof(uint64_t) + sizeof(uint32_t);

    data_buffer_ =
        static_cast<char*>(std::malloc(capacity_ * element_byte_size_));
    if (!data_buffer_) {
      throw std::runtime_error("FlatIndex: Failed to allocate memory");
    }

    if (meta_->enable_sparse) {
      sparse_index_ = std::make_unique<SparseDataHolder>();
      bool use_l2 = (meta_->distance_type == "l2");
      sparse_index_->set_params(meta_->index_with_sparse_logit_alpha,
                                meta_->search_with_sparse_logit_alpha, use_l2);
      sparse_index_->set_max_elements(meta_->max_element_count);
      sparse_index_->init_empty_data();
    }
  }

  ~BruteforceSearch() {
    if (data_buffer_) {
      std::free(data_buffer_);
    }
  }

  void add_point(const void* vector, uint64_t label,
                 FloatValSparseDatapointLowLevel* sparse_data = nullptr,
                 bool replace_deleted = false) {
    std::shared_ptr<SparseDatapoint> sparse_dp;
    if (sparse_index_ && sparse_data) {
      if (sparse_index_->make_sparse_point_by_low_level(sparse_data,
                                                        &sparse_dp) != 0) {
        throw std::runtime_error("Sparse data conversion failed");
      }
    }

    int index = -1;
    auto it = label_map_.find(label);

    if (it != label_map_.end()) {
      index = it->second;
      if (sparse_index_) {
        if (sparse_dp) {
             if (sparse_index_->update_low_level_sparse(index, sparse_dp) != 0) {
               throw std::runtime_error("Failed to update sparse data");
             }
        }
      }
    } else {
      if (sparse_index_) {
        if (current_count_ != sparse_index_->rows()) {
          throw std::runtime_error("Sparse/Dense index inconsistency");
        }
        
        if (sparse_dp) {
             if (sparse_index_->append_low_level_sparse(sparse_dp) != 0) {
                throw std::runtime_error("Failed to append sparse data");
             }
        } else {
             auto empty_dp = std::make_shared<SparseDatapoint>(std::vector<IndexT>(), std::vector<float>());
             if (sparse_index_->append_low_level_sparse(empty_dp) != 0) {
                throw std::runtime_error("Failed to append empty sparse data");
             }
        }
      }

      if (current_count_ >= capacity_) {
        resize_buffer(current_count_ * 2 + 1);
      }

      index = current_count_;
      label_map_[label] = index;
      uint32_t logical_offset = static_cast<uint32_t>(next_logical_offset_++);
      offset_map_[logical_offset] = index;

      std::memcpy(data_buffer_ + (index * element_byte_size_) +
                      vector_byte_size_ + sizeof(uint64_t),
                  &logical_offset, sizeof(uint32_t));

      current_count_++;
    }

    char* ptr = data_buffer_ + (index * element_byte_size_);
    if (vector) {
      quantizer_->encode(static_cast<const float*>(vector), meta_->dimension,
                         ptr);
    }
    std::memcpy(ptr + vector_byte_size_, &label, sizeof(uint64_t));
  }

  void remove_point(uint64_t label) {
    auto it = label_map_.find(label);
    if (it == label_map_.end())
      return;

    size_t idx_to_remove = it->second;
    size_t idx_last = current_count_ - 1;

    label_map_.erase(it);

    uint32_t offset_to_remove;
    std::memcpy(&offset_to_remove,
                data_buffer_ + (idx_to_remove * element_byte_size_) +
                    vector_byte_size_ + sizeof(uint64_t),
                sizeof(uint32_t));
    offset_map_.erase(offset_to_remove);

    if (current_count_ > 0 && idx_to_remove != idx_last) {
      char* dest = data_buffer_ + (idx_to_remove * element_byte_size_);
      char* src = data_buffer_ + (idx_last * element_byte_size_);
      std::memcpy(dest, src, element_byte_size_);

      uint64_t label_moved;
      std::memcpy(&label_moved, dest + vector_byte_size_, sizeof(uint64_t));
      label_map_[label_moved] = idx_to_remove;

      uint32_t offset_moved;
      std::memcpy(&offset_moved, dest + vector_byte_size_ + sizeof(uint64_t),
                  sizeof(uint32_t));
      offset_map_[offset_moved] = idx_to_remove;

      if (sparse_index_) {
        // SPDLOG_INFO("remove_point: swapping sparse row {} with {}", idx_to_remove, idx_last);
        auto last_row = sparse_index_->get_row(idx_last);
        if (sparse_index_->update_low_level_sparse(idx_to_remove, last_row) != 0) {
           SPDLOG_ERROR("Failed to update sparse data during remove");
        }
      }
    }

    if (sparse_index_) {
      // SPDLOG_INFO("remove_point: popping back sparse row");
      if (sparse_index_->pop_back() != 0) {
         SPDLOG_ERROR("Failed to pop back sparse data during remove");
      }
    }

    current_count_--;
  }

  void search_knn(const void* query_data, size_t k, const Bitmap* filter_bitmap,
                  FloatValSparseDatapointLowLevel* sparse,
                  std::vector<uint64_t>& labels,
                  std::vector<float>& scores) const {
    if (!query_data)
      return;
    if (k == 0) {
      labels.clear();
      scores.clear();
      return;
    }
    if (current_count_ == 0)
      return;

    auto query_sparse_view = transform_sparse_query(sparse);

    std::vector<char> encoded_query(vector_byte_size_);
    quantizer_->encode(static_cast<const float*>(query_data), meta_->dimension,
                       encoded_query.data());

    using ResultPair = std::pair<float, uint64_t>;
    std::priority_queue<ResultPair, std::vector<ResultPair>, std::greater<ResultPair>> pq;

    auto dist_func = space_->get_metric_function();
    void* dist_params = space_->get_metric_params();

    if (!filter_bitmap) {
      for (size_t i = 0; i < current_count_; ++i) {
        char* ptr = data_buffer_ + (i * element_byte_size_);

        float dist = compute_score(encoded_query.data(), ptr, query_sparse_view,
                                   i, dist_func, dist_params);

        uint64_t label;
        std::memcpy(&label, ptr + vector_byte_size_, sizeof(uint64_t));

        if (pq.size() < k) {
          pq.emplace(dist, label);
        } else if (dist > pq.top().first) {
          pq.pop();
          pq.emplace(dist, label);
        }
      }
    } else {
      if (filter_bitmap->empty()) {
         labels.clear();
         scores.clear();
         return;
      }
      std::vector<uint32_t> offsets;
      filter_bitmap->get_set_list(offsets);
      for (uint32_t offset : offsets) {
        auto it = offset_map_.find(offset);
        if (it == offset_map_.end()) {
          continue;
        }

        int idx = it->second;
        char* ptr = data_buffer_ + (idx * element_byte_size_);

        float dist = compute_score(encoded_query.data(), ptr, query_sparse_view,
                                   idx, dist_func, dist_params);

        uint64_t label;
        std::memcpy(&label, ptr + vector_byte_size_, sizeof(uint64_t));

        if (pq.size() < k) {
          pq.emplace(dist, label);
        } else if (dist > pq.top().first) {
          pq.pop();
          pq.emplace(dist, label);
        }
      }
    }

    size_t result_size = pq.size();
    labels.resize(result_size);
    scores.resize(result_size);

    for (int i = static_cast<int>(result_size) - 1; i >= 0; --i) {
      const auto& top = pq.top();
      scores[i] = top.first;
      labels[i] = top.second;
      pq.pop();
    }
  }

  void save(const std::filesystem::path& dir) {
    if (meta_) {
      meta_->element_count = current_count_;
      meta_->max_element_count = capacity_;
    }
    std::string path = (dir / kFlatIndexFileName).string();
    std::ofstream out(path, std::ios::binary);

    write_binary(out, capacity_);
    write_binary(out, element_byte_size_);
    write_binary(out, current_count_);

    out.write(data_buffer_, capacity_ * element_byte_size_);
    write_binary(out, next_logical_offset_);

    if (sparse_index_) {
      size_t dummy;
      sparse_index_->save_data(dir, dummy);
    }
  }

  void load(const std::filesystem::path& dir) {
    std::string path = (dir / kFlatIndexFileName).string();
    std::ifstream in(path, std::ios::binary);
    if (!in)
      throw std::runtime_error("Failed to open index file");

    size_t loaded_cap, loaded_elem_size;
    read_binary(in, loaded_cap);
    read_binary(in, loaded_elem_size);
    read_binary(in, current_count_);

    if (loaded_elem_size != element_byte_size_) {
      throw std::runtime_error("Element size mismatch");
    }

    resize_buffer(loaded_cap);
    in.read(data_buffer_, loaded_cap * loaded_elem_size);
    
    if (in.peek() != EOF) {
        read_binary(in, next_logical_offset_);
    } else {
        next_logical_offset_ = 0;
    }

    rebuild_maps();

    if (sparse_index_) {
      sparse_index_->load_data(dir);
    }
  }

  uint64_t get_data_num() const {
    return current_count_;
  }

  int get_offset_by_label(uint64_t label) {
    auto it = label_map_.find(label);
    if (it != label_map_.end()) {
      size_t idx = it->second;
      uint32_t offset;
      std::memcpy(&offset,
                  data_buffer_ + (idx * element_byte_size_) +
                      vector_byte_size_ + sizeof(uint64_t),
                  sizeof(uint32_t));
      return offset;
    }
    return -1;
  }

  uint64_t get_label_by_offset(int offset) {
    auto it = offset_map_.find(offset);
    if (it != offset_map_.end()) {
      int idx = it->second;
      char* ptr = data_buffer_ + (idx * element_byte_size_);
      uint64_t label;
      std::memcpy(&label, ptr + vector_byte_size_, sizeof(uint64_t));
      return label;
    }
    return -1;
  }

 private:
  void setup_metric() {
    reverse_query_score_ = (meta_->distance_type == "l2");
    if (meta_->quantization_type == "int8") {
      if (meta_->distance_type == "l2")
        space_ = std::make_unique<L2SpaceInt8>(meta_->dimension);
      else
        space_ = std::make_unique<InnerProductSpaceInt8>(meta_->dimension);
    } else {
      if (meta_->distance_type == "l2")
        space_ = std::make_unique<L2Space>(meta_->dimension);
      else
        space_ = std::make_unique<InnerProductSpace>(meta_->dimension);
    }
  }

  void resize_buffer(size_t new_cap) {
    if (new_cap < current_count_)
      return;
    char* new_buf = static_cast<char*>(
        std::realloc(data_buffer_, new_cap * element_byte_size_));
    if (!new_buf)
      throw std::runtime_error("Realloc failed");
    data_buffer_ = new_buf;
    capacity_ = new_cap;
    if (sparse_index_) {
      sparse_index_->reserve(new_cap);
    }
  }

  void rebuild_maps() {
    label_map_.clear();
    offset_map_.clear();
    uint32_t max_offset = 0;
    for (size_t i = 0; i < current_count_; ++i) {
      char* ptr = data_buffer_ + (i * element_byte_size_);
      uint64_t lbl;
      uint32_t off;
      std::memcpy(&lbl, ptr + vector_byte_size_, sizeof(uint64_t));
      std::memcpy(&off, ptr + vector_byte_size_ + sizeof(uint64_t),
                  sizeof(uint32_t));

      label_map_[lbl] = i;
      offset_map_[off] = i;
      if (off > max_offset) {
        max_offset = off;
      }
    }
    if (current_count_ > 0 && next_logical_offset_ <= max_offset) {
        next_logical_offset_ = max_offset + 1;
    }
  }

  std::shared_ptr<SparseDatapointView> transform_sparse_query(
      const FloatValSparseDatapointLowLevel* sparse) const {
    if (!meta_->search_with_sparse_logit_alpha || !sparse || !sparse_index_) {
      return nullptr;
    }
    std::shared_ptr<SparseDatapointView> view;
    sparse_index_->make_sparse_view_by_low_level(sparse, &view);
    return view;
  }

  float compute_score(
      const void* encoded_query, const char* data_ptr,
      const std::shared_ptr<SparseDatapointView>& query_sparse_view,
      size_t idx, MetricFunc<float> dist_func,
      void* dist_params) const {
    float dense_raw = dist_func(encoded_query, data_ptr, dist_params);
    float dense_score =
        reverse_query_score_ ? (1.0f - dense_raw) : dense_raw;
    if (!sparse_index_ || !query_sparse_view ||
        meta_->search_with_sparse_logit_alpha <= 0.0f) {
      return dense_score;
    }
    float sparse_raw =
        sparse_index_->sparse_head_output(*query_sparse_view, idx);
    float sparse_score =
        reverse_query_score_ ? (1.0f - sparse_raw) : sparse_raw;
    float alpha = meta_->search_with_sparse_logit_alpha;
    return dense_score * (1.0f - alpha) + sparse_score * alpha;
  }

  std::shared_ptr<BruteForceMeta> meta_;
  char* data_buffer_ = nullptr;
  size_t capacity_ = 0;
  size_t current_count_ = 0;
  size_t vector_byte_size_ = 0;
  size_t element_byte_size_ = 0;

  std::unordered_map<uint64_t, int> label_map_;
  std::unordered_map<uint32_t, int> offset_map_;

  std::unique_ptr<VectorSpace<float>> space_;
  std::unique_ptr<VectorQuantizer> quantizer_;
  std::unique_ptr<SparseDataHolder> sparse_index_;
  bool reverse_query_score_ = false;
  uint64_t next_logical_offset_ = 0;
};

}  // namespace vectordb
