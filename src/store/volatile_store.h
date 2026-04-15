// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <string>
#include <vector>
#include <map>
#include <shared_mutex>
#include "store/common_structs.h"
#include "store/kv_store.h"

namespace vectordb {

class VolatileStore : public KVStore {
 public:
  VolatileStore() = default;

  ~VolatileStore() override = default;

  int exec_op(const std::vector<StorageOp>& ops) override;

  std::vector<std::string> get_data(
      const std::vector<std::string>& keys) override;

  int put_data(const std::vector<std::string>& keys,
               const std::vector<std::string>& values) override;

  int delete_data(const std::vector<std::string>& keys) override;

  int clear_data() override;

  std::vector<std::pair<std::string, std::string>> seek_range(
      const std::string& start_key, const std::string& end_key) override;

 private:
  std::map<std::string, std::string> data_;
  mutable std::shared_mutex mutex_;
};

}  // namespace vectordb