// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <string>
#include <vector>
#include <memory>
#include <filesystem>
#include "index/detail/vector/common/bruteforce.h"
#include "index/detail/search_context.h"
#include "index/detail/vector/vector_recall.h"

namespace vectordb {

class VectorIndexAdapter {
 public:
  VectorIndexAdapter() = default;
  virtual ~VectorIndexAdapter() = default;
  virtual std::string type() const {
    return "Base";
  }

  virtual int recall(const VectorRecallRequest& request,
                     VectorRecallResult& result) = 0;

  virtual int stream_add_data(uint64_t label, const float* ebd_vec,
                              FloatValSparseDatapointLowLevel* sparse) = 0;

  virtual int stream_delete_data(uint64_t label) = 0;

  virtual int load(const std::filesystem::path& dir) = 0;

  virtual int dump(const std::filesystem::path& dir) = 0;

  virtual uint64_t get_embedding_dim() = 0;

  virtual uint64_t get_data_num() = 0;

  virtual int get_offset_by_label(const uint64_t& label) = 0;

  virtual uint64_t get_label_by_offset(const int& offset) {
    return 0;
  }
};

class BruteForceIndex : public VectorIndexAdapter {
 public:
  BruteForceIndex(std::shared_ptr<BruteForceMeta> meta)
      : meta_(meta), index_(std::make_shared<BruteforceSearch>(meta)) {
  }

  ~BruteForceIndex() = default;

  std::string type() const override {
    return "BruteForceIndex";
  }

  int recall(const VectorRecallRequest& request,
             VectorRecallResult& result) override {
    FloatValSparseDatapointLowLevel sparse_datapoint(request.sparse_terms,
                                                     request.sparse_values);
    FloatValSparseDatapointLowLevel* sparse_ptr =
        (request.sparse_terms && request.sparse_values) ? &sparse_datapoint
                                                        : nullptr;
    index_->search_knn(request.dense_vector, request.topk, request.bitmap,
                       sparse_ptr, result.labels, result.scores);
    return 0;
  }

  virtual int stream_add_data(uint64_t label, const float* ebd_vec,
                              FloatValSparseDatapointLowLevel* sparse) {
    index_->add_point(ebd_vec, label, sparse);
    return 0;
  }

  virtual int stream_delete_data(uint64_t label) {
    index_->remove_point(label);
    return 0;
  }

  virtual int load(const std::filesystem::path& dir) override {
    index_->load(dir.string());
    return 0;
  }

  virtual int dump(const std::filesystem::path& dir) {
    index_->save(dir);
    return 0;
  }

  virtual uint64_t get_embedding_dim() override {
    return meta_->dimension;
  }

  virtual uint64_t get_data_num() override {
    return index_->get_data_num();
  }

  virtual int get_offset_by_label(const uint64_t& label) {
    return index_->get_offset_by_label(label);
  }

  virtual uint64_t get_label_by_offset(const int& offset) {
    return index_->get_label_by_offset(offset);
  }

 private:
  std::shared_ptr<BruteForceMeta> meta_;
  std::shared_ptr<BruteforceSearch> index_;
};

}  // namespace vectordb