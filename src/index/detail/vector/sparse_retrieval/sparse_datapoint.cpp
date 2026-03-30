// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include "sparse_datapoint.h"
#include "common/io_utils.h"

namespace vectordb {

std::string SparseDatapoint::to_string() const {
  std::ostringstream s;
  if (indices_.size() != values_.size()) {
    s << "ERROR_SPARSE_TENSOR: indices size[" << indices_.size()
      << "] not match"
      << "vals size[" << values_.size() << "]";
    return s.str();
  }
  s << "Nonzero_Entries[" << indices_.size() << "] ";
  s << "Content:[ ";
  for (size_t i = 0; i < indices_.size(); ++i) {
    s << "[" << indices_[i] << "," << values_[i] << "] ";
  }
  s << "]";
  return s.str();
}

std::string SparseDatapointView::to_string() const {
  std::ostringstream s;
  s << "Nonzero_Entries[" << nonzero_entries_ << "] ";
  s << "Content:[ ";
  for (size_t i = 0; i < nonzero_entries_; ++i) {
    s << "[" << indices_[i] << "," << values_[i] << "] ";
  }
  s << "]";
  return s.str();
}

int SparseDatapointView::serialize(std::ostream& out) const {
  write_bin(out, nonzero_entries_);
  for (size_t i = 0; i < nonzero_entries_; ++i) {
    write_bin(out, indices_[i]);
  }
  for (size_t i = 0; i < nonzero_entries_; ++i) {
    write_bin(out, values_[i]);
  }
  return 0;
}

}  // namespace vectordb
