// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include "index/detail/vector/sparse_retrieval/sparse_datapoint.h"
#include "spdlog/spdlog.h"

namespace vectordb {

class SparseDataset {
 public:
  /*
   * SparseDataset uses CSR (Compressed Sparse Row) format.
   * Data is stored in continuous memory (flat_indices_, flat_values_).
   * offsets_ stores the start position of each row.
   * Range of row i: [offsets_[i], offsets_[i+1])
   */
  SparseDataset() {
    flat_indices_.reserve(8096);
    flat_values_.reserve(8096);
    offsets_.reserve(1024);
    offsets_.push_back(0);  // Initial offset for the first row
  }
  virtual ~SparseDataset() {
  }

  int append(const std::vector<IndexT>& indices,
             const std::vector<float>& values) {
    if (values.size() != indices.size()) {
      SPDLOG_ERROR(
          "SparseDataset::append fail, values.size(): {} != indices.size(): {}",
          values.size(), indices.size());
      return -1;
    }

    // Check for overflow of size_t (StartOffsetT replaced by size_t)
    if (entries_ + indices.size() > std::numeric_limits<size_t>::max()) {
      SPDLOG_ERROR("SparseDataset::append fail, entries count overflow");
      return -2;
    }

    flat_indices_.insert(flat_indices_.end(), indices.begin(), indices.end());
    flat_values_.insert(flat_values_.end(), values.begin(), values.end());

    entries_ += indices.size();
    offsets_.push_back(entries_);
    return 0;
  }

  int append(const SparseDatapoint& dp) {
    return append(dp.indices(), dp.values());
  }

  int update(size_t idx, const std::vector<IndexT>& indices,
             const std::vector<float>& values) {
    if (idx >= offsets_.size() - 1) {
      throw std::runtime_error("update out of bounds");
    }

    if (values.size() != indices.size()) {
      SPDLOG_ERROR("SparseDataset::update fail, size mismatch");
      return -1;
    }

    size_t old_start = offsets_[idx];
    size_t old_end = offsets_[idx + 1];
    size_t old_len = old_end - old_start;
    size_t new_len = indices.size();
    long diff = (long)new_len - (long)old_len;

    // Check overflow if growing
    if (diff > 0 && entries_ + diff > std::numeric_limits<size_t>::max()) {
      SPDLOG_ERROR("SparseDataset::update fail, entries count overflow");
      return -2;
    }

    if (new_len == old_len) {
      // In-place update
      std::copy(indices.begin(), indices.end(),
                flat_indices_.begin() + old_start);
      std::copy(values.begin(), values.end(), flat_values_.begin() + old_start);
    } else {
      // Length changed, need to resize and shift
      if (diff > 0) {
        // Expand: insert space
        flat_indices_.insert(flat_indices_.begin() + old_end, diff, 0);
        flat_values_.insert(flat_values_.begin() + old_end, diff, 0);
      } else {
        // Shrink: erase extra space
        // Note: erase range is [first, last)
        flat_indices_.erase(flat_indices_.begin() + old_start + new_len,
                            flat_indices_.begin() + old_end);
        flat_values_.erase(flat_values_.begin() + old_start + new_len,
                           flat_values_.begin() + old_end);
      }

      // Copy new data
      std::copy(indices.begin(), indices.end(),
                flat_indices_.begin() + old_start);
      std::copy(values.begin(), values.end(), flat_values_.begin() + old_start);

      // Update all subsequent offsets
      for (size_t i = idx + 1; i < offsets_.size(); ++i) {
        offsets_[i] += diff;
      }
      entries_ += diff;
    }
    return 0;
  }

  int update(size_t idx, const SparseDatapoint& dp) {
    return update(idx, dp.indices(), dp.values());
  }

  SparseDatapointView get_view(size_t i) {
    if (i >= offsets_.size() - 1) {
      throw std::runtime_error("get view out of bounds");
    }
    size_t start = offsets_[i];
    size_t end = offsets_[i + 1];
    size_t len = end - start;

    float* values_ptr = (len == 0) ? nullptr : (flat_values_.data() + start);
    IndexT* indices_ptr = (len == 0) ? nullptr : (flat_indices_.data() + start);

    return SparseDatapointView(indices_ptr, values_ptr, len);
  }

  std::shared_ptr<SparseDatapoint> get_row(size_t i) {
    if (i >= offsets_.size() - 1) {
      throw std::runtime_error("get row out of bounds");
    }
    size_t start = offsets_[i];
    size_t end = offsets_[i + 1];
    
    std::vector<IndexT> indices;
    std::vector<float> values;
    
    if (start < end) {
      indices.assign(flat_indices_.begin() + start, flat_indices_.begin() + end);
      values.assign(flat_values_.begin() + start, flat_values_.begin() + end);
    }
    
    return std::make_shared<SparseDatapoint>(std::move(indices), std::move(values));
  }

  int pop_back() {
    if (offsets_.size() <= 1) {
      return 0; // Already empty (just initial offset 0)
    }
    
    size_t last_start = offsets_[offsets_.size() - 2];
    size_t last_end = offsets_.back();
    size_t count = last_end - last_start;
    
    if (count > 0) {
      flat_indices_.erase(flat_indices_.begin() + last_start, flat_indices_.end());
      flat_values_.erase(flat_values_.begin() + last_start, flat_values_.end());
    }
    
    offsets_.pop_back();
    entries_ -= count;
    
    return 0;
  }

  void reserve(size_t n_points) {
    offsets_.reserve(n_points + 1);
  }

  void reserve(size_t max_points, size_t n_entries) {
    offsets_.reserve(max_points + 1);
    flat_indices_.reserve(n_entries);
    flat_values_.reserve(n_entries);
  }

  void clear() {
    flat_indices_.clear();
    flat_values_.clear();
    offsets_.clear();
    offsets_.push_back(0);
    entries_ = 0;
  }

  size_t size() {
    return offsets_.size() - 1;
  }

  size_t size(size_t idx) {
    return offsets_[idx + 1] - offsets_[idx];
  }

  size_t entries() {
    return entries_;
  }

  size_t capacity() {
    return flat_indices_.capacity();
  }

 private:
  size_t entries_ = 0;
  std::vector<IndexT> flat_indices_;
  std::vector<float> flat_values_;
  std::vector<size_t> offsets_;
};

}  // namespace vectordb
