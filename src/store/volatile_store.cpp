// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include "volatile_store.h"
#include "spdlog/spdlog.h"
#include <stdexcept>
#include <mutex>
#include <shared_mutex>

namespace vectordb {

std::vector<std::string> VolatileStore::get_data(
    const std::vector<std::string>& keys) {
  std::shared_lock<std::shared_mutex> lock(mutex_);
  std::vector<std::string> values(keys.size());
  for (size_t i = 0; i < keys.size(); ++i) {
    auto iter = data_.find(keys[i]);
    if (iter == data_.end()) {
      continue;
    }
    values[i] = iter->second;
  }
  return values;
}

int VolatileStore::put_data(const std::vector<std::string>& keys,
                            const std::vector<std::string>& values) {
  std::unique_lock<std::shared_mutex> lock(mutex_);
  for (size_t i = 0; i < keys.size(); ++i) {
    data_[keys[i]] = values[i];
  }
  return 0;
}

int VolatileStore::delete_data(const std::vector<std::string>& keys) {
  std::unique_lock<std::shared_mutex> lock(mutex_);
  for (const auto& key : keys) {
    data_.erase(key);
  }
  return 0;
}

int VolatileStore::clear_data() {
  std::unique_lock<std::shared_mutex> lock(mutex_);
  data_.clear();
  return 0;
}

std::vector<std::pair<std::string, std::string>> VolatileStore::seek_range(
    const std::string& start_key, const std::string& end_key) {
  std::shared_lock<std::shared_mutex> lock(mutex_);
  std::vector<std::pair<std::string, std::string>> key_values;
  for (auto iter = data_.lower_bound(start_key);
       iter != data_.end() && iter->first < end_key; ++iter) {
    key_values.push_back({iter->first, iter->second});
  }
  return key_values;
}

int VolatileStore::exec_op(const std::vector<StorageOp>& ops) {
  std::unique_lock<std::shared_mutex> lock(mutex_);
  for (const auto& op : ops) {
    if (op.type == StorageOp::PUT_OP) {
      data_[op.key] = op.value;
    } else if (op.type == StorageOp::DELETE_OP) {
      data_.erase(op.key);
    } else {
      SPDLOG_WARN("Unknown op type: {}", static_cast<int>(op.type));
      continue;
    }
  }
  return 0;
}

}  // namespace vectordb