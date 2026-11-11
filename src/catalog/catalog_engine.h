// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include <memory>
#include <string>

namespace vectordb {

// Collection-owned explicit declarations; effective values are resolved in C++.
class CatalogEngine {
 public:
  explicit CatalogEngine(const std::string& path);
  ~CatalogEngine();
  std::string execute(const std::string& request_json);

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace vectordb
