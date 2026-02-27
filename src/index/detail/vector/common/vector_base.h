// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstdint>
#include <iostream>
#include <vector>

// Platform & SIMD Detection
#if defined(__x86_64__) || defined(_M_X64) || defined(__i386__) || \
    defined(_M_IX86)
#define OV_PLATFORM_X86
#include <immintrin.h>
#if defined(__AVX512F__) && !defined(OV_DISABLE_AVX512)
#define OV_SIMD_AVX512
#endif
#if defined(__AVX__)
#define OV_SIMD_AVX
#endif
#if defined(__SSE3__) || defined(__SSE4_2__) || defined(__SSE__)
#define OV_SIMD_SSE
#endif
#endif

// ARM Platform Detection
#if defined(__aarch64__) || defined(_M_ARM64)
#define OV_PLATFORM_ARM
#define OV_SIMD_NEON
#if defined(__ARM_FEATURE_SVE)
#define OV_SIMD_SVE
#endif
#endif

// Memory Alignment Macros
#if defined(_MSC_VER)
#define OV_ALIGN_32 __declspec(align(32))
#define OV_ALIGN_64 __declspec(align(64))
#else
#define OV_ALIGN_32 __attribute__((aligned(32)))
#define OV_ALIGN_64 __attribute__((aligned(64)))
#endif

namespace vectordb {

using LabelType = uint64_t;

// Distance metric function signature
// params usually points to dimension
template <typename T>
using MetricFunc = T (*)(const void* vec1, const void* vec2,
                         const void* params);

// Abstract base class for vector spaces
// Defines how vectors are stored and compared
template <typename T>
class VectorSpace {
 public:
  virtual ~VectorSpace() = default;

  // Returns size in bytes required to store a single vector
  virtual size_t get_vector_byte_size() const = 0;

  // Returns the distance calculation function
  virtual MetricFunc<T> get_metric_function() const = 0;

  // Returns parameters for distance calculation (e.g. dimension)
  virtual void* get_metric_params() const = 0;
};

// Binary I/O Helpers
template <typename T>
void write_binary(std::ostream& out, const T& val) {
  out.write(reinterpret_cast<const char*>(&val), sizeof(T));
}

template <typename T>
void read_binary(std::istream& in, T& val) {
  in.read(reinterpret_cast<char*>(&val), sizeof(T));
}

}  // namespace vectordb
