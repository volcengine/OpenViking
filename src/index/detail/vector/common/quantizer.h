// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include <cstring>
#include <memory>
#include <string>
#include <stdexcept>
#include "index/detail/vector/common/quantization_int8.h"

namespace vectordb {

class VectorQuantizer {
 public:
  virtual ~VectorQuantizer() = default;
  virtual void encode(const float* vec, size_t dim, void* dest) const = 0;
  virtual size_t get_encoded_size() const = 0;
};

class Float32Quantizer : public VectorQuantizer {
 public:
  explicit Float32Quantizer(size_t dim) : dim_(dim) {
  }

  void encode(const float* vec, size_t dim, void* dest) const override {
    if (!vec || !dest)
      throw std::runtime_error("Float32Quantizer: null pointer");
    std::memcpy(dest, vec, dim * sizeof(float));
  }

  size_t get_encoded_size() const override {
    return dim_ * sizeof(float);
  }

 private:
  size_t dim_;
};

class Int8Quantizer : public VectorQuantizer {
 public:
  Int8Quantizer(size_t dim, const std::string& distance_type)
      : dim_(dim), distance_type_(distance_type) {
  }

  void encode(const float* vec, size_t dim, void* dest) const override {
    if (!vec || !dest)
      throw std::runtime_error("Int8Quantizer: null pointer");
    bool compute_norm_sq = (distance_type_ == "l2");
    quantize_vector_int8(vec, dim, dest, compute_norm_sq);
  }

  size_t get_encoded_size() const override {
    // IP: data + scale (4 bytes)
    // L2: data + scale (4 bytes) + norm_sq (4 bytes)
    size_t metadata_size =
        (distance_type_ == "l2") ? 2 * sizeof(float) : sizeof(float);
    return dim_ * sizeof(int8_t) + metadata_size;
  }

 private:
  size_t dim_;
  std::string distance_type_;
};

inline std::unique_ptr<VectorQuantizer> createQuantizer(
    const std::string& quantization_type, const std::string& distance_type,
    size_t dimension) {
  if (quantization_type == "int8") {
    return std::make_unique<Int8Quantizer>(dimension, distance_type);
  } else if (quantization_type == "float" || quantization_type.empty()) {
    return std::make_unique<Float32Quantizer>(dimension);
  } else {
    throw std::runtime_error("Unknown quantization type: " + quantization_type);
  }
}

}  // namespace vectordb
