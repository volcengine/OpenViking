// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "vector_base.h"
#include <cmath>

namespace vectordb {

// Basic L2 squared distance implementation
static float l2_sqr_ref(const void* v1, const void* v2, const void* params) {
  const float* pv1 = static_cast<const float*>(v1);
  const float* pv2 = static_cast<const float*>(v2);
  size_t dim = *static_cast<const size_t*>(params);

  float res = 0;
  for (size_t i = 0; i < dim; ++i) {
    float diff = pv1[i] - pv2[i];
    res += diff * diff;
  }
  return res;
}

#if defined(OV_SIMD_AVX512)
static float l2_sqr_avx512(const void* v1, const void* v2, const void* params) {
  const float* pv1 = static_cast<const float*>(v1);
  const float* pv2 = static_cast<const float*>(v2);
  size_t dim = *static_cast<const size_t*>(params);

  __m512 sum = _mm512_setzero_ps();
  size_t i = 0;

  // Process 16 floats at a time
  for (; i + 16 <= dim; i += 16) {
    __m512 a = _mm512_loadu_ps(pv1 + i);
    __m512 b = _mm512_loadu_ps(pv2 + i);
    __m512 diff = _mm512_sub_ps(a, b);
    sum = _mm512_fmadd_ps(diff, diff, sum);
  }

  float res = _mm512_reduce_add_ps(sum);

  // Handle remaining elements
  for (; i < dim; ++i) {
    float diff = pv1[i] - pv2[i];
    res += diff * diff;
  }

  return res;
}
#endif

#if defined(OV_SIMD_AVX)
static float l2_sqr_avx(const void* v1, const void* v2, const void* params) {
  const float* pv1 = static_cast<const float*>(v1);
  const float* pv2 = static_cast<const float*>(v2);
  size_t dim = *static_cast<const size_t*>(params);

  __m256 sum = _mm256_setzero_ps();
  size_t i = 0;

  // Process 8 floats at a time
  for (; i + 8 <= dim; i += 8) {
    __m256 a = _mm256_loadu_ps(pv1 + i);
    __m256 b = _mm256_loadu_ps(pv2 + i);
    __m256 diff = _mm256_sub_ps(a, b);
    sum = _mm256_fmadd_ps(diff, diff, sum);
  }

  // Reduce AVX register
  __m128 sum_low = _mm256_extractf128_ps(sum, 0);
  __m128 sum_high = _mm256_extractf128_ps(sum, 1);
  __m128 sum128 = _mm_add_ps(sum_low, sum_high);

  // Horizontal add
  sum128 = _mm_hadd_ps(sum128, sum128);
  sum128 = _mm_hadd_ps(sum128, sum128);

  float res = _mm_cvtss_f32(sum128);

  // Handle remaining elements
  for (; i < dim; ++i) {
    float diff = pv1[i] - pv2[i];
    res += diff * diff;
  }

  return res;
}
#endif

#if defined(OV_SIMD_SSE)
static float l2_sqr_sse(const void* v1, const void* v2, const void* params) {
  const float* pv1 = static_cast<const float*>(v1);
  const float* pv2 = static_cast<const float*>(v2);
  size_t dim = *static_cast<const size_t*>(params);

  __m128 sum = _mm_setzero_ps();
  size_t i = 0;

  // Process 4 floats at a time
  for (; i + 4 <= dim; i += 4) {
    __m128 a = _mm_loadu_ps(pv1 + i);
    __m128 b = _mm_loadu_ps(pv2 + i);
    __m128 diff = _mm_sub_ps(a, b);
    sum = _mm_add_ps(sum, _mm_mul_ps(diff, diff));
  }

  // Horizontal add
  sum = _mm_hadd_ps(sum, sum);
  sum = _mm_hadd_ps(sum, sum);

  float res = _mm_cvtss_f32(sum);

  // Handle remaining elements
  for (; i < dim; ++i) {
    float diff = pv1[i] - pv2[i];
    res += diff * diff;
  }

  return res;
}
#endif

class L2Space : public VectorSpace<float> {
 public:
  explicit L2Space(size_t dim) : dim_(dim) {
    // Select best implementation at runtime based on compile-time flags
    // In a real scenario, we might want dynamic dispatch based on CPUID
#if defined(OV_SIMD_AVX512)
    metric_func_ = l2_sqr_avx512;
#elif defined(OV_SIMD_AVX)
    metric_func_ = l2_sqr_avx;
#elif defined(OV_SIMD_SSE)
    metric_func_ = l2_sqr_sse;
#else
    metric_func_ = l2_sqr_ref;
#endif
  }

  size_t get_vector_byte_size() const override {
    return dim_ * sizeof(float);
  }

  MetricFunc<float> get_metric_function() const override {
    return metric_func_;
  }

  void* get_metric_params() const override {
    return const_cast<size_t*>(&dim_);
  }

 private:
  size_t dim_;
  MetricFunc<float> metric_func_;
};

}  // namespace vectordb
