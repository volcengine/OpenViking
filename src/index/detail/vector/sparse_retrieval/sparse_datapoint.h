// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include <cmath>
#include <cstdint>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace vectordb {

using DimensionIndex = uint32_t;
using IndexT = uint32_t;
using TermKey = uint64_t;

struct SparseDatapointView;

struct SparseDatapoint {
  SparseDatapoint() {
  }

  SparseDatapoint(SparseDatapoint&&) = default;
  SparseDatapoint& operator=(SparseDatapoint&&) = default;
  SparseDatapoint(const SparseDatapoint&) = default;
  SparseDatapoint& operator=(const SparseDatapoint&) = default;

  SparseDatapoint(std::vector<IndexT> indices, std::vector<float> values)
      : indices_(std::move(indices)), values_(std::move(values)) {
  }

  void clear() {
    indices_.clear();
    values_.clear();
  }

  void reserve(size_t sz) {
    indices_.reserve(sz);
    values_.reserve(sz);
  }

  void resize(size_t sz) {
    indices_.resize(sz);
    values_.resize(sz);
  }

  const std::vector<IndexT>& indices() const {
    return indices_;
  }

  std::vector<IndexT>* mutable_indices() {
    return &indices_;
  }

  const std::vector<float>& values() const {
    return values_;
  }

  std::vector<float>* mutable_values() {
    return &values_;
  }

  bool has_values() const {
    return values_.size() > 0;
  }

  DimensionIndex nonzero_entries() const {
    return indices_.size();
  }

  std::string to_string() const;

  SparseDatapointView to_ptr() const;

 private:
  std::vector<IndexT> indices_;

  std::vector<float> values_;
};

struct SparseDatapointView {
  SparseDatapointView() {
  }

  SparseDatapointView(const IndexT* indices, const float* values,
                      DimensionIndex nonzero_entries)
      : indices_(indices), values_(values), nonzero_entries_(nonzero_entries) {
  }

  const IndexT* indices() const {
    return indices_;
  }

  void reset(const IndexT* indices, const float* values) {
    indices_ = indices;
    values_ = values;
  }

  void reset(const IndexT* indices, const float* values,
             DimensionIndex nonzero_entries) {
    indices_ = indices;
    values_ = values;
    nonzero_entries_ = nonzero_entries;
  }

  std::string to_string() const;

  int serialize(std::ostream& out) const;

  const float* values() const {
    return values_;
  }

  bool has_values() const {
    return values_;
  }

  DimensionIndex nonzero_entries() const {
    return nonzero_entries_;
  }

  const IndexT* indices_ = nullptr;

  const float* values_ = nullptr;

  DimensionIndex nonzero_entries_ = 0;
};

inline SparseDatapointView SparseDatapoint::to_ptr() const {
  return SparseDatapointView(indices_.data(), values_.data(),
                             nonzero_entries());
}

static SparseDatapointView make_sparse_datapoint_view(
    const SparseDatapoint& dp) {
  return SparseDatapointView(dp.indices().data(), dp.values().data(),
                             dp.nonzero_entries());
}

}  // namespace vectordb
