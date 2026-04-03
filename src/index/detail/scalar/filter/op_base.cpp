// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include "op_base.h"
#include "spdlog/spdlog.h"

namespace vectordb {

const std::string SorterOpBase::kOrderDescStr = "desc";
const std::string SorterOpBase::kOrderAscStr = "asc";
const std::string SorterOpBase::kTypeCenter1d = "center1d";

int parse_and_precheck_op_parts(JsonDoc& json_doc, bool& has_filter,
                                bool& has_sorter) {
  has_filter = false;
  has_sorter = false;

  if (json_doc.HasParseError() || !json_doc.IsObject()) {
    return -1;
  }
  bool has_any = false;
  if (json_doc.HasMember("filter")) {
    has_filter = true;
    has_any = true;
  }
  if (json_doc.HasMember("sorter")) {
    has_sorter = true;
    has_any = true;
  }

  if (!has_any) {
    // Backward compatibility: default to filter when no top-level keyword exists
    has_filter = true;
  }
  return 0;
}
}  // namespace vectordb