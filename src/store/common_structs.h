// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

#pragma once
#include <string>

namespace vectordb {

struct StorageOp {
  enum OpType {
    PUT_OP = 0,
    DELETE_OP = 1,
  };

  OpType type = PUT_OP;
  std::string key;
  std::string value;
};

}  // namespace vectordb
