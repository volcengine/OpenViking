// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#pragma once
#include "spdlog/spdlog.h"
#include "common/zip_sort.h"
#include "index/detail/vector/sparse_retrieval/sparse_dataset.h"
#include "index/detail/vector/sparse_retrieval/sparse_datapoint.h"
#include "index/detail/vector/sparse_retrieval/sparse_distance_measure.h"
#include <unordered_map>
#include <filesystem>
#include <thread>

namespace vectordb {

using DocID = size_t;
using ValueT = float;

class SparseRowIndex {
  // CSR (Compressed Sparse Row) storage format for sparse vectors
 public:
  SparseRowIndex() {
    sparse_dataset_ = std::make_shared<SparseDataset>();
  }

  virtual ~SparseRowIndex() {
  }

  int clear() {
    index_term_.clear();
    term_index_.clear();
    sparse_dataset_->clear();
    finish_populate_terms_ = false;
    return 0;
  }

  int save_data(const std::filesystem::path& save_dir,
                size_t& estimate_malloc_mem_bytes);

  int load_data(const std::filesystem::path& load_dir, size_t max_elements = 0);

  int init_empty_data(size_t max_elements);

  void reserve(size_t max_elements) {
    max_elements_ = max_elements;
    if (sparse_dataset_) {
      sparse_dataset_->reserve(max_elements_);
    }
  }

  int populate_terms(const std::vector<TermKey>& terms,
                     bool check_finish = true);

  size_t rows() const {
    return sparse_dataset_->size();
  }

  ValueT sparse_dot_product_reduce(const SparseDatapointView& x,
                                   const DocID docid) {
    const SparseDatapointView& doc_ts = sparse_dataset_->get_view(docid);
    return sparse_dist_measure::sparse_distance(
        doc_ts, x, sparse_dist_measure::DotProductReduceTwo(),
        sparse_dist_measure::DoNothingReduce());
  }

  ValueT sparse_squared_l2_reduce(const SparseDatapointView& x,
                                  const DocID docid) {
    const SparseDatapointView& doc_ts = sparse_dataset_->get_view(docid);
    return sparse_dist_measure::sparse_distance(
        doc_ts, x, sparse_dist_measure::SquaredL2ReduceTwo(),
        sparse_dist_measure::SquaredL2ReduceOne());
  }

  int append(const std::vector<IndexT>& indices,
             const std::vector<ValueT>& values) {
    int ret = sparse_dataset_->append(indices, values);
    if (ret) {
      SPDLOG_ERROR("SparseRowIndex append failed, with ret={}", ret);
    }
    return ret;
  }

  int append(const SparseDatapoint& dp) {
    int ret = sparse_dataset_->append(dp);
    if (ret) {
      SPDLOG_ERROR("SparseRowIndex append failed, with ret={}", ret);
    }
    return ret;
  }

  int update(size_t idx, const SparseDatapoint& dp) {
    int ret = sparse_dataset_->update(idx, dp);
    if (ret) {
      SPDLOG_ERROR("SparseRowIndex append failed, with ret={}", ret);
    }
    return ret;
  }

  SparseDatapointView get_view(DocID i) {
    return sparse_dataset_->get_view(i);
  }

  std::shared_ptr<SparseDatapoint> get_row(DocID i) {
    return sparse_dataset_->get_row(i);
  }

  int pop_back() {
    return sparse_dataset_->pop_back();
  }

  int append_term_vals(const std::vector<TermKey>& terms,
                       const std::vector<ValueT>& values);

  // Generate sparse vector while adding unseen terms to term_index
  int index_by_terms(const std::vector<TermKey>& terms,
                     const std::vector<ValueT>& values,
                     std::vector<IndexT>& mutable_indices,
                     std::vector<ValueT>& mutable_values);

 protected:
  bool finish_populate_terms_ = false;
  std::vector<TermKey> index_term_;
  std::unordered_map<TermKey, IndexT> term_index_;
  std::shared_ptr<SparseDataset>
      sparse_dataset_;  // only support ValueT type for now
  size_t max_elements_;

  size_t base_elements_;
  size_t base_capacity_;
};

}  // namespace vectordb
