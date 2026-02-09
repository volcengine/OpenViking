// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#include "persist_store.h"
#include "spdlog/spdlog.h"
#include <stdexcept>
#include <filesystem>
#include "leveldb/write_batch.h"

namespace vectordb {

PersistStore::PersistStore(const std::string& path) {
  leveldb::Options options;
  options.create_if_missing = true;  // Create database if it doesn't exist
  std::error_code ec;
  std::filesystem::create_directories(path, ec);
  if (ec) {
    throw std::runtime_error(
        "PersistStore::PersistStore create_directories failed, path=" + path);
  }
  auto status = leveldb::DB::Open(options, path, &db_);
  if (!status.ok()) {
    SPDLOG_ERROR("Failed to open data db: {}", status.ToString());
    throw std::runtime_error(status.ToString());
  }
  SPDLOG_DEBUG("PersistStore init success, path: {}", path);
}
PersistStore::~PersistStore() {
  delete db_;
}

std::vector<std::string> PersistStore::get_data(
    const std::vector<std::string>& keys) {
  std::vector<std::string> values(keys.size());
  leveldb::ReadOptions options;
  const leveldb::Snapshot* snapshot = db_->GetSnapshot();
  options.snapshot = snapshot;

  for (size_t i = 0; i < keys.size(); ++i) {
    auto status = db_->Get(options, keys[i], &values[i]);
    if (!status.ok()) {
      if (!status.IsNotFound()) {
        SPDLOG_WARN("Failed to get data for key {}: {}", keys[i],
                    status.ToString());
      }
      continue;
    }
  }
  db_->ReleaseSnapshot(snapshot);
  return values;
}

int PersistStore::put_data(const std::vector<std::string>& keys,
                           const std::vector<std::string>& values) {
  leveldb::WriteBatch batch;
  for (size_t i = 0; i < keys.size(); ++i) {
    batch.Put(keys[i], values[i]);
  }
  leveldb::WriteOptions write_options;
  write_options.sync = true;
  auto status = db_->Write(write_options, &batch);
  if (!status.ok()) {
    SPDLOG_WARN("Failed to put data db: {}", status.ToString());
    return -1;
  }
  return 0;
}

int PersistStore::delete_data(const std::vector<std::string>& keys) {
  leveldb::WriteBatch batch;
  for (const auto& key : keys) {
    batch.Delete(key);
  }
  leveldb::WriteOptions write_options;
  write_options.sync = true;
  auto status = db_->Write(write_options, &batch);
  if (!status.ok()) {
    SPDLOG_WARN("Failed to delete data db: {}", status.ToString());
    return -1;
  }
  return 0;
}

int PersistStore::clear_data() {
  leveldb::WriteBatch batch;
  leveldb::Iterator* it = db_->NewIterator(leveldb::ReadOptions());
  for (it->SeekToFirst(); it->Valid(); it->Next()) {
    batch.Delete(it->key().ToString());
  }
  leveldb::WriteOptions write_options;
  write_options.sync = true;
  auto status = db_->Write(write_options, &batch);
  if (!status.ok()) {
    SPDLOG_WARN("Failed to clear data db: {}", status.ToString());
    return -1;
  }
  return 0;
}

std::vector<std::pair<std::string, std::string>> PersistStore::seek_range(
    const std::string& start_key, const std::string& end_key) {
  std::vector<std::pair<std::string, std::string>> key_values;
  leveldb::Iterator* it = db_->NewIterator(leveldb::ReadOptions());
  for (it->Seek(start_key); it->Valid() && it->key().ToString() < end_key;
       it->Next()) {
    key_values.push_back({it->key().ToString(), it->value().ToString()});
  }
  if (!it->status().ok()) {
    SPDLOG_WARN("PersistStore::seek_range iterate error: {}",
                it->status().ToString());
  }
  delete it;
  return key_values;
}

int PersistStore::exec_op(const std::vector<StorageOp>& ops) {
  leveldb::WriteBatch batch;
  for (const auto& op : ops) {
    if (op.type == StorageOp::PUT_OP) {
      batch.Put(op.key, op.value);
    } else if (op.type == StorageOp::DELETE_OP) {
      batch.Delete(op.key);
    } else {
      SPDLOG_WARN("Unknown op type: {}", static_cast<int>(op.type));
      continue;
    }
  }
  leveldb::WriteOptions write_options;
  write_options.sync = true;
  auto status = db_->Write(write_options, &batch);
  if (!status.ok()) {
    SPDLOG_WARN("Failed to exec op data db: {}", status.ToString());
    return -1;
  }
  return 0;
}

}  // namespace vectordb