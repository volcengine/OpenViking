// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <cstddef>
#include <string>
#include <vector>
#include "store/common_structs.h"

namespace vectordb {

class KVStore {
 public:
  virtual ~KVStore() = default;

  virtual int exec_op(const std::vector<StorageOp>& ops) = 0;

  virtual std::vector<std::string> get_data(
      const std::vector<std::string>& keys) = 0;

  virtual int put_data(const std::vector<std::string>& keys,
                       const std::vector<std::string>& values) = 0;

  virtual int delete_data(const std::vector<std::string>& keys) = 0;

  virtual int clear_data() = 0;

  virtual std::vector<std::pair<std::string, std::string>> seek_range(
      const std::string& start_key, const std::string& end_key) = 0;

  virtual std::vector<std::pair<std::string, std::string>> seek_range_page(
      const std::string& start_key, const std::string& end_key, size_t limit,
      size_t max_bytes, bool start_exclusive) = 0;
};

}  // namespace vectordb
