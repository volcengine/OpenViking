// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include <cstdint>
#include <cmath>
#include <algorithm>

namespace vectordb {

inline void quantize_vector_int8(const float* vec, size_t dim, void* dest,
                                 bool compute_norm_sq = true) {
  int8_t* dest_int8 = static_cast<int8_t*>(dest);

  float max_abs = 0.0f;
  for (size_t i = 0; i < dim; i++) {
    float abs_val = std::fabs(vec[i]);
    if (abs_val > max_abs) {
      max_abs = abs_val;
    }
  }

  float scale = (max_abs > 1e-8f) ? (max_abs / 127.0f) : 1.0f;
  float inv_scale = 1.0f / scale;

  for (size_t i = 0; i < dim; i++) {
    float quantized_val = vec[i] * inv_scale;
    quantized_val = std::max(-127.0f, std::min(127.0f, quantized_val));
    dest_int8[i] = static_cast<int8_t>(std::round(quantized_val));
  }

  float* metadata_ptr = reinterpret_cast<float*>(dest_int8 + dim);
  metadata_ptr[0] = scale;

  if (compute_norm_sq) {
    float norm_sq = 0.0f;
    for (size_t i = 0; i < dim; i++) {
      norm_sq += vec[i] * vec[i];
    }
    metadata_ptr[1] = norm_sq;
  }
}

inline void dequantize_vector_int8(const int8_t* quantized, size_t dim,
                                   float scale, float* out_vec) {
  for (size_t i = 0; i < dim; i++) {
    out_vec[i] = static_cast<float>(quantized[i]) * scale;
  }
}

}  // namespace vectordb
