// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "vector_base.h"
#include <cmath>

namespace vectordb {

static float inner_product_ref(const void* v1, const void* v2,
                               const void* params) {
  const float* pv1 = static_cast<const float*>(v1);
  const float* pv2 = static_cast<const float*>(v2);
  size_t dim = *static_cast<const size_t*>(params);

  float res = 0;
  for (size_t i = 0; i < dim; ++i) {
    res += pv1[i] * pv2[i];
  }
  return res;
}

#if defined(OV_SIMD_AVX512)
static float inner_product_avx512(const void* v1, const void* v2,
                                  const void* params) {
  const float* pv1 = static_cast<const float*>(v1);
  const float* pv2 = static_cast<const float*>(v2);
  size_t dim = *static_cast<const size_t*>(params);

  __m512 sum = _mm512_setzero_ps();
  size_t i = 0;

  for (; i + 16 <= dim; i += 16) {
    __m512 a = _mm512_loadu_ps(pv1 + i);
    __m512 b = _mm512_loadu_ps(pv2 + i);
    sum = _mm512_add_ps(sum, _mm512_mul_ps(a, b));
  }

  float res = _mm512_reduce_add_ps(sum);

  for (; i < dim; ++i) {
    res += pv1[i] * pv2[i];
  }

  return res;
}
#endif

#if defined(OV_SIMD_AVX)
static float inner_product_avx(const void* v1, const void* v2,
                               const void* params) {
  const float* pv1 = static_cast<const float*>(v1);
  const float* pv2 = static_cast<const float*>(v2);
  size_t dim = *static_cast<const size_t*>(params);

  __m256 sum = _mm256_setzero_ps();
  size_t i = 0;

  for (; i + 8 <= dim; i += 8) {
    __m256 a = _mm256_loadu_ps(pv1 + i);
    __m256 b = _mm256_loadu_ps(pv2 + i);
    sum = _mm256_add_ps(sum, _mm256_mul_ps(a, b));
  }

  __m128 sum_low = _mm256_extractf128_ps(sum, 0);
  __m128 sum_high = _mm256_extractf128_ps(sum, 1);
  __m128 sum128 = _mm_add_ps(sum_low, sum_high);

  sum128 = _mm_hadd_ps(sum128, sum128);
  sum128 = _mm_hadd_ps(sum128, sum128);

  float res = _mm_cvtss_f32(sum128);

  for (; i < dim; ++i) {
    res += pv1[i] * pv2[i];
  }

  return res;
}
#endif

#if defined(OV_SIMD_SSE)
static float inner_product_sse(const void* v1, const void* v2,
                               const void* params) {
  const float* pv1 = static_cast<const float*>(v1);
  const float* pv2 = static_cast<const float*>(v2);
  size_t dim = *static_cast<const size_t*>(params);

  __m128 sum = _mm_setzero_ps();
  size_t i = 0;

  for (; i + 4 <= dim; i += 4) {
    __m128 a = _mm_loadu_ps(pv1 + i);
    __m128 b = _mm_loadu_ps(pv2 + i);
    sum = _mm_add_ps(sum, _mm_mul_ps(a, b));
  }

  sum = _mm_hadd_ps(sum, sum);
  sum = _mm_hadd_ps(sum, sum);

  float res = _mm_cvtss_f32(sum);

  for (; i < dim; ++i) {
    res += pv1[i] * pv2[i];
  }

  return res;
}
#endif

#if defined(OV_SIMD_NEON)
#include "krl.h"

// ARM NEON optimized inner product using KRL library
static float inner_product_neon(const void* v1, const void* v2,
                                const void* params) {
  const float* pv1 = static_cast<const float*>(v1);
  const float* pv2 = static_cast<const float*>(v2);
  size_t dim = *static_cast<const size_t*>(params);
  float dis = 0;
  krl_ipdis(pv1, pv2, dim, &dis, 1);
  return dis;
}
#endif

class InnerProductSpace : public VectorSpace<float> {
 public:
  explicit InnerProductSpace(size_t dim) : dim_(dim) {
#if defined(OV_SIMD_NEON)
    metric_func_ = inner_product_neon;
#elif defined(OV_SIMD_AVX512)
    metric_func_ = inner_product_avx512;
#elif defined(OV_SIMD_AVX)
    metric_func_ = inner_product_avx;
#elif defined(OV_SIMD_SSE)
    metric_func_ = inner_product_sse;
#else
    metric_func_ = inner_product_ref;
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
