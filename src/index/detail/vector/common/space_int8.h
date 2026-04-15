// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include "vector_base.h"
#include "quantization_int8.h"
#include <cstdint>
#include <algorithm>
#include <cmath>

#if defined(OV_SIMD_AVX) || defined(OV_SIMD_AVX512_VNNI) || defined(OV_SIMD_AMX)
#include <immintrin.h>
#endif
#if defined(OV_SIMD_AMX)
#include <cstring>
#if defined(__linux__)
#include <sys/syscall.h>
#include <unistd.h>
#endif
#endif

namespace vectordb {

#if defined(OV_SIMD_AMX)
// Linux requires explicit permission for AMX tile data via arch_prctl.
// This must be called once per thread before any AMX tile instruction.
inline void ensure_amx_permission() {
  static bool requested = false;
  if (!requested) {
#if defined(__linux__)
    // ARCH_REQ_XCOMP_PERM = 0x1023, XFEATURE_XTILEDATA = 18
    syscall(SYS_arch_prctl, 0x1023, 18);
#endif
    requested = true;
  }
}
#endif

static int32_t inner_product_int8_scalar(const void* v1, const void* v2,
                                         const void* params) {
  const int8_t* pv1 = static_cast<const int8_t*>(v1);
  const int8_t* pv2 = static_cast<const int8_t*>(v2);
  size_t dim = *static_cast<const size_t*>(params);

  int32_t sum = 0;
  for (size_t i = 0; i < dim; ++i) {
    sum += static_cast<int32_t>(pv1[i]) * static_cast<int32_t>(pv2[i]);
  }
  return sum;
}

#if defined(OV_SIMD_AVX)
static int32_t inner_product_int8_avx(const void* v1, const void* v2,
                                      const void* params) {
  const int8_t* pv1 = static_cast<const int8_t*>(v1);
  const int8_t* pv2 = static_cast<const int8_t*>(v2);
  size_t dim = *static_cast<const size_t*>(params);

  size_t dim32 = (dim / 32) * 32;
  __m256i sum_vec = _mm256_setzero_si256();

  for (size_t i = 0; i < dim32; i += 32) {
    __m256i vec1 = _mm256_loadu_si256((__m256i*)(pv1 + i));
    __m256i vec2 = _mm256_loadu_si256((__m256i*)(pv2 + i));

    // Split into low and high 128-bit lanes
    __m128i v1_lo = _mm256_castsi256_si128(vec1);
    __m128i v1_hi = _mm256_extracti128_si256(vec1, 1);
    __m128i v2_lo = _mm256_castsi256_si128(vec2);
    __m128i v2_hi = _mm256_extracti128_si256(vec2, 1);

    // Extend to 16-bit
    __m256i v1_lo_16 = _mm256_cvtepi8_epi16(v1_lo);
    __m256i v2_lo_16 = _mm256_cvtepi8_epi16(v2_lo);
    __m256i v1_hi_16 = _mm256_cvtepi8_epi16(v1_hi);
    __m256i v2_hi_16 = _mm256_cvtepi8_epi16(v2_hi);

    // Multiply 16-bit integers
    __m256i prod_lo = _mm256_mullo_epi16(v1_lo_16, v2_lo_16);
    __m256i prod_hi = _mm256_mullo_epi16(v1_hi_16, v2_hi_16);

    // Extend to 32-bit and accumulate
    __m256i prod_lo_lo32 =
        _mm256_cvtepi16_epi32(_mm256_castsi256_si128(prod_lo));
    __m256i prod_lo_hi32 =
        _mm256_cvtepi16_epi32(_mm256_extracti128_si256(prod_lo, 1));
    __m256i prod_hi_lo32 =
        _mm256_cvtepi16_epi32(_mm256_castsi256_si128(prod_hi));
    __m256i prod_hi_hi32 =
        _mm256_cvtepi16_epi32(_mm256_extracti128_si256(prod_hi, 1));

    sum_vec = _mm256_add_epi32(sum_vec, prod_lo_lo32);
    sum_vec = _mm256_add_epi32(sum_vec, prod_lo_hi32);
    sum_vec = _mm256_add_epi32(sum_vec, prod_hi_lo32);
    sum_vec = _mm256_add_epi32(sum_vec, prod_hi_hi32);
  }

  // Horizontal sum
  __m128i sum_hi = _mm256_extracti128_si256(sum_vec, 1);
  __m128i sum_lo = _mm256_castsi256_si128(sum_vec);
  __m128i sum128 = _mm_add_epi32(sum_lo, sum_hi);

  // Extract values
  int32_t OV_ALIGN_32 temp[4];
  _mm_store_si128((__m128i*)temp, sum128);
  int32_t sum = temp[0] + temp[1] + temp[2] + temp[3];

  // Process remaining elements
  for (size_t i = dim32; i < dim; ++i) {
    sum += static_cast<int32_t>(pv1[i]) * static_cast<int32_t>(pv2[i]);
  }

  return sum;
}
#endif

#if defined(OV_SIMD_AVX512_VNNI)
// AVX-512 VNNI kernel: uses vpdpbusd (uint8 × int8 → int32)
// Processes 64 int8 pairs per iteration via 512-bit VNNI dot-product.
// Since vpdpbusd treats src1 as unsigned, we XOR with 0x80 to convert
// signed int8 to unsigned, then correct: result -= 128 * sum(pv2).
static int32_t inner_product_int8_avx512_vnni(const void* v1, const void* v2,
                                               const void* params) {
  const int8_t* pv1 = static_cast<const int8_t*>(v1);
  const int8_t* pv2 = static_cast<const int8_t*>(v2);
  size_t dim = *static_cast<const size_t*>(params);

  const __m512i sign_flip = _mm512_set1_epi8(static_cast<char>(0x80));
  const __m512i ones_u8 = _mm512_set1_epi8(1);

  __m512i acc = _mm512_setzero_si512();
  __m512i sum_b = _mm512_setzero_si512();

  size_t i = 0;
  const size_t dim64 = (dim / 64) * 64;

  for (; i < dim64; i += 64) {
    __m512i a = _mm512_loadu_si512(pv1 + i);
    __m512i b = _mm512_loadu_si512(pv2 + i);

    // Convert a from signed to unsigned: a_u = a_s ^ 0x80 (== a_s + 128)
    __m512i a_u = _mm512_xor_si512(a, sign_flip);

    // acc += a_u * b  (unsigned × signed → int32, 4-way within each lane)
    acc = _mm512_dpbusd_epi32(acc, a_u, b);

    // Track sum(b) for correction: ones_u8 * b sums groups of 4 signed bytes
    sum_b = _mm512_dpbusd_epi32(sum_b, ones_u8, b);
  }

  // Horizontal reductions
  int32_t result = _mm512_reduce_add_epi32(acc);
  int32_t corr = _mm512_reduce_add_epi32(sum_b);
  result -= 128 * corr;

  // Process remaining elements
  for (; i < dim; ++i) {
    result += static_cast<int32_t>(pv1[i]) * static_cast<int32_t>(pv2[i]);
  }

  return result;
}
#endif

#if defined(OV_SIMD_AMX)
// AMX tile configuration structure (64 bytes, must be 64-byte aligned)
struct OV_ALIGN_64 AmxTileCfg {
  uint8_t palette_id;
  uint8_t start_row;
  uint8_t reserved_0[14];
  uint16_t colsb[16];
  uint8_t rows[16];
};

// Batch inner product using AMX TDPBSSD (signed int8 × signed int8 → int32).
// Computes dot(db[i], query) for i=0..num_vecs-1 simultaneously using tiles.
// db_base:  pointer to int8 data of the first vector in the block
// stride:   bytes between consecutive vectors (element_byte_size_)
// query:    query int8 data (contiguous)
// results:  output int32 dot products [num_vecs]
// num_vecs: vectors in this block (1-16)
// dim:      vector dimension
static void batch_inner_product_int8_amx(
    const char* db_base, size_t stride,
    const int8_t* query,
    int32_t* results,
    size_t num_vecs,
    size_t dim) {

  ensure_amx_permission();

  const size_t dim64 = (dim / 64) * 64;

  if (dim64 > 0) {
    // Configure tiles:
    //   Tile 0 (C/dst):  num_vecs rows × 4 bytes  (1 int32 per row)
    //   Tile 1 (A/src1): num_vecs rows × 64 bytes (64 int8s per row)
    //   Tile 2 (B/src2): 16 rows × 4 bytes        (K/4 × N*4)
    AmxTileCfg cfg = {};
    cfg.palette_id = 1;
    cfg.rows[0] = static_cast<uint8_t>(num_vecs);
    cfg.colsb[0] = 4;
    cfg.rows[1] = static_cast<uint8_t>(num_vecs);
    cfg.colsb[1] = 64;
    cfg.rows[2] = 16;
    cfg.colsb[2] = 4;

    _tile_loadconfig(&cfg);
    _tile_zero(0);

    for (size_t k = 0; k < dim64; k += 64) {
      // A: db vectors block, each row is a 64-byte chunk of one vector
      _tile_loadd(1, db_base + k, stride);
      // B: query chunk, 16 rows × 4 bytes (natural byte layout, stride=4)
      _tile_loadd(2, query + k, 4);
      // C += A × B  (signed int8 × signed int8 → int32)
      _tile_dpbssd(0, 1, 2);
    }

    // Store tile C to buffer
    int32_t OV_ALIGN_64 c_buf[16] = {};
    _tile_stored(0, c_buf, 4);
    _tile_release();

    for (size_t i = 0; i < num_vecs; ++i) {
      results[i] = c_buf[i];
    }
  } else {
    std::memset(results, 0, num_vecs * sizeof(int32_t));
  }

  // Handle remaining dimensions (dim64..dim) with scalar
  for (size_t k = dim64; k < dim; ++k) {
    for (size_t i = 0; i < num_vecs; ++i) {
      const int8_t* db_vec =
          reinterpret_cast<const int8_t*>(db_base + i * stride);
      results[i] +=
          static_cast<int32_t>(db_vec[k]) * static_cast<int32_t>(query[k]);
    }
  }
}

// Multi-query AMX batch inner product.
// Computes dot(db[i], queries[q]) for i=0..num_vecs-1, q=0..nq-1
// simultaneously using one TDPBSSD per 64-dim chunk.
// results layout (row-major): results[i * nq + q] = dot(db[i], queries[q])
// num_vecs: DB vectors in this block (1-16)
// nq:       number of queries (1-16)
static void batch_inner_product_int8_amx_multi_query(
    const char* db_base, size_t stride,
    const int8_t* const* queries,
    int32_t* results,
    size_t num_vecs,
    size_t nq,
    size_t dim) {

  ensure_amx_permission();

  const size_t dim64 = (dim / 64) * 64;
  const size_t b_stride = nq * 4;   // bytes per row of B tile
  const size_t c_stride = nq * 4;   // bytes per row of C tile

  if (dim64 > 0) {
    AmxTileCfg cfg = {};
    cfg.palette_id = 1;
    // Tile 0 (C): num_vecs rows × nq int32 cols
    cfg.rows[0] = static_cast<uint8_t>(num_vecs);
    cfg.colsb[0] = static_cast<uint16_t>(c_stride);
    // Tile 1 (A): num_vecs rows × 64 bytes
    cfg.rows[1] = static_cast<uint8_t>(num_vecs);
    cfg.colsb[1] = 64;
    // Tile 2 (B): 16 rows × nq*4 bytes (VNNI interleaved)
    cfg.rows[2] = 16;
    cfg.colsb[2] = static_cast<uint16_t>(b_stride);

    // B tile buffer: 16 rows × max 16 queries × 4 bytes = 1024 bytes max
    int8_t OV_ALIGN_64 b_buf[16 * 16 * 4];
    int32_t OV_ALIGN_64 c_buf[16 * 16] = {};

    _tile_loadconfig(&cfg);
    _tile_zero(0);

    for (size_t k = 0; k < dim64; k += 64) {
      // Prepare B tile: interleave query data into VNNI layout
      // b_buf[row * b_stride + q * 4 + j] = queries[q][k + row * 4 + j]
      for (size_t row = 0; row < 16; ++row) {
        for (size_t q = 0; q < nq; ++q) {
          std::memcpy(b_buf + row * b_stride + q * 4,
                      queries[q] + k + row * 4, 4);
        }
      }

      _tile_loadd(1, db_base + k, stride);
      _tile_loadd(2, b_buf, b_stride);
      _tile_dpbssd(0, 1, 2);
    }

    _tile_stored(0, c_buf, c_stride);
    _tile_release();

    for (size_t i = 0; i < num_vecs; ++i) {
      for (size_t q = 0; q < nq; ++q) {
        results[i * nq + q] = c_buf[i * nq + q];
      }
    }
  } else {
    std::memset(results, 0, num_vecs * nq * sizeof(int32_t));
  }

  // Handle remaining dimensions with scalar
  for (size_t k = dim64; k < dim; ++k) {
    for (size_t i = 0; i < num_vecs; ++i) {
      const int8_t* db_vec =
          reinterpret_cast<const int8_t*>(db_base + i * stride);
      for (size_t q = 0; q < nq; ++q) {
        results[i * nq + q] +=
            static_cast<int32_t>(db_vec[k]) *
            static_cast<int32_t>(queries[q][k]);
      }
    }
  }
}
#endif

// Distance functions
static float inner_product_distance_int8(const void* v1, const void* v2,
                                         const void* params) {
  size_t dim = *static_cast<const size_t*>(params);

  // Extract metadata (scale)
  // Layout: [int8 data (dim)] [scale (float)]
  const float* scale1_ptr =
      reinterpret_cast<const float*>(static_cast<const int8_t*>(v1) + dim);
  const float* scale2_ptr =
      reinterpret_cast<const float*>(static_cast<const int8_t*>(v2) + dim);

  float scale1 = *scale1_ptr;
  float scale2 = *scale2_ptr;

  int32_t ip;
#if defined(OV_SIMD_AVX512_VNNI)
  ip = inner_product_int8_avx512_vnni(v1, v2, params);
#elif defined(OV_SIMD_AVX)
  if (dim >= 32) {
    ip = inner_product_int8_avx(v1, v2, params);
  } else {
    ip = inner_product_int8_scalar(v1, v2, params);
  }
#else
  ip = inner_product_int8_scalar(v1, v2, params);
#endif

  float real_ip = static_cast<float>(ip) * scale1 * scale2;
  return real_ip;
}

static float l2_distance_int8(const void* v1, const void* v2,
                              const void* params) {
  size_t dim = *static_cast<const size_t*>(params);

  // Extract metadata (scale, norm_sq)
  // Layout: [int8 data (dim)] [scale (float)] [norm_sq (float)]
  const float* meta1 =
      reinterpret_cast<const float*>(static_cast<const int8_t*>(v1) + dim);
  const float* meta2 =
      reinterpret_cast<const float*>(static_cast<const int8_t*>(v2) + dim);

  float scale1 = meta1[0];
  float norm_sq1 = meta1[1];

  float scale2 = meta2[0];
  float norm_sq2 = meta2[1];

  int32_t ip;
#if defined(OV_SIMD_AVX512_VNNI)
  ip = inner_product_int8_avx512_vnni(v1, v2, params);
#elif defined(OV_SIMD_AVX)
  if (dim >= 32) {
    ip = inner_product_int8_avx(v1, v2, params);
  } else {
    ip = inner_product_int8_scalar(v1, v2, params);
  }
#else
  ip = inner_product_int8_scalar(v1, v2, params);
#endif

  float real_ip = static_cast<float>(ip) * scale1 * scale2;
  float dist = norm_sq1 + norm_sq2 - 2.0f * real_ip;

  return std::max(0.0f, dist);
}

class InnerProductSpaceInt8 : public VectorSpace<float> {
 public:
  explicit InnerProductSpaceInt8(size_t dim) : dim_(dim) {
    metric_func_ = inner_product_distance_int8;
  }

  size_t get_vector_byte_size() const override {
    // data + scale
    return dim_ * sizeof(int8_t) + sizeof(float);
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

class L2SpaceInt8 : public VectorSpace<float> {
 public:
  explicit L2SpaceInt8(size_t dim) : dim_(dim) {
    metric_func_ = l2_distance_int8;
  }

  size_t get_vector_byte_size() const override {
    // data + scale + norm_sq
    return dim_ * sizeof(int8_t) + 2 * sizeof(float);
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
