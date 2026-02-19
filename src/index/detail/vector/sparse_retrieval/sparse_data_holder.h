// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

#pragma once
#include <vector>

#include "spdlog/spdlog.h"
#include "index/detail/vector/sparse_retrieval/sparse_row_index.h"
#include "index/detail/vector/sparse_retrieval/sparse_datapoint.h"
#include "common/ann_utils.h"

namespace vectordb {
// sparse retrieval
class SparseDataHolder {
  typedef float (SparseDataHolder::*sparse_logit_func)(
      const SparseDatapointView&, const DocID);

 public:
  SparseDataHolder() {
  }

  virtual ~SparseDataHolder() {
  }

 public:
  int load_data(const std::filesystem::path& load_dir) {
    return sparse_holder_.load_data(load_dir, max_elements_);
  }

  int save_data(const std::filesystem::path& save_dir,
                size_t& estimate_malloc_mem_bytes) {
    return sparse_holder_.save_data(save_dir, estimate_malloc_mem_bytes);
  }

  int init_empty_data() {
    return sparse_holder_.init_empty_data(max_elements_);
  }

  void set_max_elements(size_t max_elements) {
    max_elements_ = max_elements;
  }

  void reserve(size_t max_elements) {
    max_elements_ = max_elements;
    sparse_holder_.reserve(max_elements);
  }

  void set_params(const bool index_use_sparse, const bool search_use_sparse,
                  const bool search_use_l2 = false) {
    index_with_sparse_bias_ = index_use_sparse;
    search_with_sparse_bias_ = search_use_sparse;
    index_with_sparse_bias_alpha_ =
        index_with_sparse_bias_ ? 0.5 : 0.0;  // Default equal weight addition
    search_with_sparse_bias_alpha_ = search_with_sparse_bias_ ? 0.5 : 0.0;
    if (search_use_l2) {
      sparse_logit_func_ = &SparseDataHolder::sparse_head_squared_l2_logit;
    } else {
      sparse_logit_func_ = &SparseDataHolder::sparse_head_dot_product_logit;
    }
  }

  void set_params(const float index_with_sparse_bias_alpha,
                  const float search_with_sparse_bias_alpha,
                  const bool search_use_l2 = false, size_t max_elements = 0) {
    index_with_sparse_bias_ =
        (index_with_sparse_bias_alpha != 0.0 ? true : false);
    search_with_sparse_bias_ =
        (search_with_sparse_bias_alpha != 0.0 ? true : false);  // For compatibility
    index_with_sparse_bias_alpha_ = index_with_sparse_bias_alpha;
    search_with_sparse_bias_alpha_ = search_with_sparse_bias_alpha;
    if (search_use_l2) {
      sparse_logit_func_ = &SparseDataHolder::sparse_head_squared_l2_logit;
    } else {
      sparse_logit_func_ = &SparseDataHolder::sparse_head_dot_product_logit;
    }
    max_elements_ = max_elements;
  }

  int populate_raw_terms(const std::vector<std::string>& raw_terms,
                         bool check_finish = true) {
    std::vector<TermKey> hash_terms;
    hash_terms.reserve(raw_terms.size());
    for (const auto& term : raw_terms) {
      hash_terms.emplace_back(std::hash<std::string>{}(term));
    }
    return populate_terms(hash_terms, check_finish);
  }

  int populate_terms(const std::vector<TermKey>& terms,
                     bool check_finish = true) {
    return sparse_holder_.populate_terms(terms, check_finish);
  }

  int append_term_vals(const std::vector<TermKey>& terms,
                       const std::vector<float>& values) {
    return sparse_holder_.append_term_vals(terms, values);
  }

  int append_raw_term_vals(const std::vector<std::string>& raw_terms,
                           const std::vector<float>& values) {
    std::vector<TermKey> hash_terms;
    hash_terms.reserve(raw_terms.size());
    for (const auto& term : raw_terms) {
      hash_terms.emplace_back(std::hash<std::string>{}(term));
    }
    return append_term_vals(hash_terms, values);
  }

  int append_low_level_sparse(const FloatValSparseDatapointLowLevel* sparse) {
    std::shared_ptr<SparseDatapoint> sparse_dp;
    if (make_sparse_point_by_low_level(sparse, &sparse_dp)) {
      return -1;
    }
    return sparse_holder_.append(*sparse_dp);
  }

  int append_low_level_sparse(std::shared_ptr<SparseDatapoint> sparse_dp) {
    return sparse_holder_.append(*sparse_dp);
  }

  int update_low_level_sparse(size_t idx,
                              std::shared_ptr<SparseDatapoint> sparse_dp) {
    return sparse_holder_.update(idx, *sparse_dp);
  }

  std::shared_ptr<SparseDatapoint> get_row(DocID i) {
    return sparse_holder_.get_row(i);
  }

  int pop_back() {
    return sparse_holder_.pop_back();
  }

  float sparse_head_output(const SparseDatapointView& x, const DocID docid) {
    return (this->*sparse_logit_func_)(x, docid);
  }

  float sparse_head_output(DocID x, DocID y) {
    const auto& x_view = sparse_holder_.get_view(x);
    return (this->*sparse_logit_func_)(x_view, y);
  }

  float sparse_head_squared_l2_logit(const SparseDatapointView& x,
                                     const DocID docid) {
    return sparse_holder_.sparse_squared_l2_reduce(x, docid);
  }

  float sparse_head_dot_product_logit(const SparseDatapointView& x,
                                      const DocID docid) {
    return sparse_holder_.sparse_dot_product_reduce(x, docid);
  }

  size_t rows() {
    return sparse_holder_.rows();
  }

  // For datapoint encode
  int make_sparse_point_by_low_level(
      const FloatValSparseDatapointLowLevel* query_sparse,
      std::shared_ptr<SparseDatapoint>* sparse_dp) {
    std::vector<IndexT> indices;
    std::vector<float> values;
    if (!query_sparse) {
      SPDLOG_ERROR(
          "make_sparse_view_by_low_level failed: query_sparse is null");
      return -1;
    }

    if (query_sparse->raw_terms &&
        query_sparse->raw_terms->size() != query_sparse->values->size()) {
      SPDLOG_ERROR(
          "make_sparse_view_by_low_level failed: raw_terms size not match {}!={}",
          query_sparse->raw_terms->size(), query_sparse->values->size());
      return -4;
    }

    if (query_sparse->raw_terms) {
      std::vector<TermKey> hash_terms;
      hash_terms.reserve(query_sparse->raw_terms->size());
      for (const auto& term : *query_sparse->raw_terms) {
        hash_terms.emplace_back(std::hash<std::string>{}(term));
      }

      // cast values to float
      std::vector<float> query_values;
      query_values.reserve(query_sparse->values->size());
      for (auto v : *(query_sparse->values)) {
        query_values.push_back(static_cast<float>(v));
      }

      sparse_holder_.index_by_terms(hash_terms, query_values, indices, values);
      *sparse_dp = std::make_shared<SparseDatapoint>(std::move(indices),
                                                     std::move(values));
    } else {
      SPDLOG_ERROR("make_sparse_view_by_low_level some logits wrong");
      return -6;
    }
    return 0;
  }

  // For query encode
  int make_sparse_view_by_low_level(
      const FloatValSparseDatapointLowLevel* query_sparse,
      std::shared_ptr<SparseDatapointView>* sparse_view) {
    thread_local std::vector<IndexT> indices;
    indices.clear();
    thread_local std::vector<float> values;
    values.clear();
    if (!query_sparse) {
      SPDLOG_ERROR(
          "make_sparse_view_by_low_level failed: query_sparse is null");
      return -1;
    }

    if (query_sparse->raw_terms &&
        query_sparse->raw_terms->size() != query_sparse->values->size()) {
      SPDLOG_ERROR(
          "make_sparse_view_by_low_level failed: raw_terms size not match {}!={}",
          query_sparse->raw_terms->size(), query_sparse->values->size());
      return -4;
    }

    if (query_sparse->raw_terms) {
      std::vector<TermKey> hash_terms;
      hash_terms.reserve(query_sparse->raw_terms->size());
      for (const auto& term : *query_sparse->raw_terms) {
        hash_terms.emplace_back(std::hash<std::string>{}(term));
      }

      // cast values to float
      std::vector<float> query_values;
      query_values.reserve(query_sparse->values->size());
      for (auto v : *(query_sparse->values)) {
        query_values.push_back(static_cast<float>(v));
      }

      sparse_holder_.index_by_terms(hash_terms, query_values, indices, values);
      *sparse_view = std::make_shared<SparseDatapointView>(
          indices.data(), values.data(), indices.size());
    } else {
      SPDLOG_ERROR("make_sparse_view_by_low_level some logits wrong");
      return -6;
    }
    return 0;
  }

  size_t max_elements_;
  bool index_with_sparse_bias_ = false;
  bool search_with_sparse_bias_ = false;
  float index_with_sparse_bias_alpha_ = 0.0;
  float search_with_sparse_bias_alpha_ = 0.0;

  sparse_logit_func sparse_logit_func_;
  SparseRowIndex sparse_holder_;
};

}  // namespace vectordb
