// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <string>
#include <vector>
#include <memory>
#include "common/json_utils.h"

namespace vectordb {

class ScalarIndexMeta {
 public:
  struct ScalarIndexItem {
    std::string field_type;
  };

  std::map<std::string, ScalarIndexItem> items;  // field_name -> index_type

  int init_from_json(const JsonValue& json);

  int init_from_file(const std::string& file_path);

  int save_to_json(JsonPrettyWriter& writer);

  int save_to_file(const std::string& file_path);
};

using ScalarIndexMetaPtr = std::shared_ptr<ScalarIndexMeta>;

}  // namespace vectordb