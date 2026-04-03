// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include "index/detail/vector/sparse_retrieval/sparse_row_index.h"
#include "common/io_utils.h"
#include <algorithm>
#include <cerrno>
#include <cstring>
namespace vectordb {

const std::string bin_filename = "sparse_retrieval_row_base.bin";

int SparseRowIndex::save_data(const std::filesystem::path& save_dir,
                              size_t& estimate_malloc_mem_bytes) {
  if (!finish_populate_terms_) {
    SPDLOG_ERROR("SparseRowIndex save data before finish build terms");
    return -1;
  }
  auto tmp_data_path = save_dir / bin_filename;
  uint64_t rows = sparse_dataset_->size();
  uint64_t cols = index_term_.size();
  size_t entries = sparse_dataset_->entries();
  uint64_t avg_entries = entries;
  if (rows > (uint64_t)std::numeric_limits<uint32_t>::max()) {
    SPDLOG_ERROR("SparseRowIndex save data failed, rows={} > u32.max", rows);
    return -2;
  }
  if (rows > 0) {
    avg_entries = entries / rows + 1;
  } else {
    avg_entries = 50;
  }

  std::ofstream output(tmp_data_path, std::ios::binary);
  write_bin(output, rows);
  write_bin(output, cols);
  write_bin(output, avg_entries);
  for (uint64_t i = 0; i < cols; ++i) {
    write_bin(output, index_term_[i]);
  }
  size_t write_succ = 0;
  for (uint32_t i = 0; i < (uint32_t)rows; ++i) {
    write_bin(output, i);
    const auto& doc_ts = sparse_dataset_->get_view(i);
    if (!doc_ts.serialize(output)) {
      ++write_succ;
    }
  }
  output.close();
  if (output.fail()) {
    SPDLOG_ERROR("SparseRowIndex save failed, file system error: {}",
                 tmp_data_path.string());
    return -3;
  }

  int64_t data_file_size = file_size(tmp_data_path);
  // index_term_
  estimate_malloc_mem_bytes += cols * sizeof(TermKey);
  // term_index_
  estimate_malloc_mem_bytes += cols * (sizeof(TermKey) + sizeof(IndexT) + 26.6);
  // start_offsets_, values_, indices_
  estimate_malloc_mem_bytes +=
      ((rows + 1) * sizeof(size_t) +
       rows * avg_entries * (sizeof(IndexT) + sizeof(ValueT)));
  SPDLOG_DEBUG("SparseRowIndex save {} write succ {} rows", save_dir.string(),
               write_succ);
  return 0;
}

int SparseRowIndex::load_data(const std::filesystem::path& load_dir,
                              size_t max_elements) {
  auto data_path = load_dir / bin_filename;
  if (!std::filesystem::exists(load_dir) ||
      !std::filesystem::exists(data_path)) {
    SPDLOG_ERROR("SparseRowIndex load_data data dir path {} not exists",
                 load_dir.string());
    return -1;
  }
  std::ifstream input(data_path, std::ios::binary);
  if (!input.is_open()) {
    SPDLOG_ERROR("SparseRowIndex load_data failed {}, error: {}.",
                 data_path.string(), std::strerror(errno));
    return -2;
  }

  sparse_dataset_ = std::make_shared<SparseDataset>();
  uint64_t rows;
  uint64_t cols;
  uint64_t avg_entries;
  read_bin(input, rows);
  read_bin(input, cols);
  read_bin(input, avg_entries);
  SPDLOG_DEBUG(
      "SparseRowIndex load from file begin:"
      "rows={}, cols={}, avg_entries={}",
      rows, cols, avg_entries);
  size_t read_succ = 0;
  max_elements_ = std::max(max_elements, size_t(rows));
  size_t term_index_buffer = std::max(size_t(100000), index_term_.size() * 2);
  index_term_.reserve(term_index_buffer);
  term_index_.reserve(term_index_buffer);
  for (uint64_t ii = 0; ii < cols; ++ii) {
    TermKey tmp_term;
    read_bin(input, tmp_term);
    if (term_index_.find(tmp_term) != term_index_.end()) {
      SPDLOG_ERROR("SparseRowIndex load data failed: term duplicate");
      input.close();
      return -4;
    }
    term_index_[tmp_term] = ii;
    index_term_.emplace_back(tmp_term);
  }
  finish_populate_terms_ = true;
  sparse_dataset_->reserve(max_elements_, max_elements_ * avg_entries);
  IndexT entries;
  SparseDatapoint tmp_dp;
  for (uint32_t ii = 0; ii < rows; ++ii) {
    uint32_t idx;
    read_bin(input, idx);
    if (idx != ii) {
      SPDLOG_ERROR("SparseRowIndex load data failed: illegal bytes {}!={}", idx,
                   ii);
      return -4;
    }
    read_bin(input, entries);
    if (entries > cols) {
      SPDLOG_ERROR(
          "SparseRowIndex load data failed,"
          "there are point with entries={} but cols={}",
          entries, cols);
      input.close();
      return -5;
    }
    tmp_dp.clear();
    tmp_dp.resize(entries);
    input.read((char*)(tmp_dp.mutable_indices()->data()),
               entries * sizeof(IndexT));
    input.read((char*)(tmp_dp.mutable_values()->data()),
               entries * sizeof(ValueT));
    int ret = append(tmp_dp);
    if (ret) {
      SPDLOG_ERROR(
          "SparseRowIndex load data failed,"
          "there are datapoint append faield, ret = {}",
          ret);
    }
    ++read_succ;
  }
  input.close();

  SPDLOG_DEBUG("SparseRowIndex load succ {}", read_succ);
  return 0;
}

int SparseRowIndex::init_empty_data(size_t max_elements) {
  sparse_dataset_ = std::make_shared<SparseDataset>();
  max_elements_ = max_elements;
  size_t term_index_buffer = std::max(size_t(100000), index_term_.size() * 2);
  index_term_.reserve(term_index_buffer);
  term_index_.reserve(term_index_buffer);
  finish_populate_terms_ = true;
  sparse_dataset_->reserve(max_elements_, max_elements * 50);
  return 0;
}

int SparseRowIndex::populate_terms(const std::vector<TermKey>& terms,
                                   bool check_finish) {
  if (check_finish && finish_populate_terms_) {
    SPDLOG_ERROR("SparseRowIndex has already build terms");
    return -1;
  }
  IndexT idx = index_term_.size();
  index_term_.reserve(terms.size());
  bool dup_term_key = false;
  for (size_t i = 0; i < terms.size(); ++i) {
    const auto& term = terms[i];
    if (term_index_.find(term) != term_index_.end()) {
      dup_term_key = true;
      continue;
    }
    index_term_.emplace_back(term);
    term_index_[term] = idx;
    idx++;
  }
  if (dup_term_key) {  // Avoid excessive duplicate log messages
    SPDLOG_WARN("SparseRowIndex build terms dup term key");
  }
  finish_populate_terms_ = true;
  return 0;
}

int SparseRowIndex::append_term_vals(const std::vector<TermKey>& terms,
                                     const std::vector<ValueT>& values) {
  if (terms.size() != values.size()) {
    SPDLOG_ERROR(
        "SparseRowIndex append_term_vals populate size not match {}!={}",
        terms.size(), values.size());
    return -1;
  }
  if (!finish_populate_terms_) {
    SPDLOG_ERROR(
        "SparseRowIndex append_term_vals but have not finish build terms");
    return -1;
  }
  std::vector<ValueT> tmp_values;
  std::vector<IndexT> temp_idxs;
  temp_idxs.reserve(tmp_values.size());
  tmp_values.reserve(tmp_values.size());
  std::unordered_map<TermKey, int> add_term_set;
  uint32_t invalid_term_cnt = 0;
  for (size_t ii = 0; ii < terms.size(); ++ii) {
    const auto& term = terms[ii];
    const auto& val = values[ii];
    if (term_index_.find(term) != term_index_.end()) {
      if (add_term_set.find(term) == add_term_set.end()) {
        add_term_set[term] = temp_idxs.size();
        temp_idxs.emplace_back(term_index_[term]);
        tmp_values.emplace_back(val);
      } else {
        tmp_values[add_term_set[term]] += val;
      }
    } else {
      invalid_term_cnt++;
    }
  }
  if (invalid_term_cnt > 0) {
    SPDLOG_ERROR("SparseRowIndex append_term_vals invalid cnt {}, tot {}",
                 invalid_term_cnt, terms.size());
  }
  ZipSortBranchOptimized(std::less<IndexT>(), temp_idxs.begin(),
                         temp_idxs.end(), tmp_values.begin(), tmp_values.end());
  return append(temp_idxs, tmp_values);
}

int SparseRowIndex::index_by_terms(const std::vector<TermKey>& terms,
                                   const std::vector<ValueT>& values,
                                   std::vector<IndexT>& mutable_indices,
                                   std::vector<ValueT>& mutable_values) {
  if (terms.size() != values.size()) {
    SPDLOG_ERROR("SparseRowIndex index_by_terms populate size not match {}!={}",
                 terms.size(), values.size());
    return -1;
  }
  mutable_indices.clear();
  mutable_values.clear();
  if (!finish_populate_terms_) {
    SPDLOG_ERROR(
        "SparseRowIndex index_by_terms but have not finish build terms");
    return -2;
  }
  mutable_indices.reserve(values.size());
  mutable_values.reserve(values.size());
  std::unordered_map<TermKey, IndexT> add_term_set;
  for (size_t ii = 0; ii < terms.size(); ++ii) {
    const auto& term = terms[ii];
    const auto& val = values[ii];
    bool term_found = false;
    {
      if (term_index_.find(term) != term_index_.end()) {
        if (add_term_set.find(term) == add_term_set.end()) {
          add_term_set[term] = mutable_indices.size();
          mutable_indices.emplace_back(term_index_[term]);
          mutable_values.emplace_back(val);
        } else {
          mutable_values[add_term_set[term]] += val;
        }
        term_found = true;
      }
    }

    if (!term_found) {
      {
        if (term_index_.find(term) == term_index_.end()) {
          term_index_[term] = IndexT(term_index_.size());
          index_term_.push_back(term);
        }
      }
      if (add_term_set.find(term) == add_term_set.end()) {
        add_term_set[term] = mutable_indices.size();
        mutable_indices.emplace_back(term_index_[term]);
        mutable_values.emplace_back(val);
      } else {
        mutable_values[add_term_set[term]] += val;
      }
    }
  }
  ZipSortBranchOptimized(std::less<IndexT>(), mutable_indices.begin(),
                         mutable_indices.end(), mutable_values.begin(),
                         mutable_values.end());
  return 0;
}

}  // namespace vectordb
