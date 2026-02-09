// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <vector>
#include <string>
#include <cstdint>
#include "index/detail/search_context.h"
#include "index/detail/scalar/bitmap_holder/bitmap.h"

namespace vectordb {

struct VectorRecallRequest {
  const float* dense_vector = nullptr;
  uint64_t topk = 0;
  const Bitmap* bitmap = nullptr;

  // Sparse vector data (optional)
  const std::vector<std::string>* sparse_terms = nullptr;
  const std::vector<float>* sparse_values = nullptr;
};

struct VectorRecallResult {
  std::vector<uint64_t> labels;
  std::vector<float> scores;
};

}  // namespace vectordb
