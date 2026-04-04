// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <vector>
#include <memory>
#include "json_utils.h"

namespace vectordb {
struct RecallResult {
  std::vector<float> scores;
  std::vector<uint64_t> labels_u64;
  std::vector<uint32_t> offsets;
  JsonDocPtr dsl_op_extra_json;

  inline int swap_offsets_vec(std::vector<float>& new_scores_container,
                              std::vector<uint32_t>& new_offsets_container) {
    new_offsets_container.swap(offsets);
    new_scores_container.swap(scores);
    return 0;
  }

  void merge_dsl_op_extra_json(const JsonValue& json_value) {
    if (dsl_op_extra_json == nullptr) {
      dsl_op_extra_json = std::make_shared<JsonDoc>();
      dsl_op_extra_json->SetObject();
    }
    merge_json_values(dsl_op_extra_json.get(), json_value,
                      dsl_op_extra_json->GetAllocator());
  }
};

struct FloatValSparseDatapointLowLevel {
  const std::vector<float>* values = nullptr;
  const std::vector<std::string>* raw_terms = nullptr;
  double query_sparse_logit_alpha = -1.0;

  FloatValSparseDatapointLowLevel(const std::vector<std::string>* raw_terms,
                                  const std::vector<float>* values)
      : raw_terms(raw_terms), values(values) {
  }
};

using RecallResultPtr = std::shared_ptr<RecallResult>;

}  // namespace vectordb