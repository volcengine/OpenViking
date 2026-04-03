// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <string>
#include "common/json_utils.h"

namespace vectordb {

class VectorIndexMeta {
 public:
  std::string distance_type = "ip";
  std::string index_type;
  std::string quantization_type = "float";  // "float" | "int8"
  uint64_t element_count = 0;
  uint64_t max_element_count = 0;
  uint64_t dimension = 0;
  bool enable_sparse = false;
  float search_with_sparse_logit_alpha = 0.0;
  float index_with_sparse_logit_alpha = 0.0;

  virtual ~VectorIndexMeta() = default;

  virtual int init_from_json(const JsonValue& json);

  virtual int save_to_file(const std::string& file_path);

  virtual int init_from_file(const std::string& file_path);

  virtual int save_to_json(JsonPrettyWriter& writer);
};

using VectorIndexMetaPtr = std::shared_ptr<VectorIndexMeta>;

}  // namespace vectordb