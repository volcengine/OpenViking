// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include <memory>
#include <optional>
#include <string>

namespace vectordb {

class IndexEngine;
class KVStore;
class BytesRow;
struct SearchRequest;

// Collection-owned explicit declarations; effective values are resolved in C++.
class CatalogEngine {
 public:
  explicit CatalogEngine(const std::string& path);
  ~CatalogEngine();
  std::string execute(const std::string& request_json);
  // Caller holds a collection Store/index snapshot for the duration of this scan.
  std::string aggregate_filter(IndexEngine& index, KVStore& candidates,
                                const BytesRow& row,
                                const std::string& request_json);
  // Returns no result if the account's Catalog revision changed during recall.
  std::optional<std::string> search_items(IndexEngine& index, KVStore& candidates,
                                          const BytesRow& row, SearchRequest query,
                                          const std::string& request_json);

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace vectordb
