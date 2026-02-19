// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <atomic>
#include <deque>
#include <functional>
#include <list>
#include <map>
#include <memory>
#include <mutex>
#include <queue>
#include <set>
#include <sstream>
#include <unordered_map>
#include <vector>
#include <croaring/roaring.hh>

namespace vectordb {

class Bitmap final {
 public:
  Bitmap() = default;
  ~Bitmap() = default;

  Bitmap(const Bitmap& other) {
    copy(other);
  }

  Bitmap& operator=(const Bitmap& other) {
    if (this != std::addressof(other)) {
      copy(other);
    }
    return *this;
  }

  Bitmap(Bitmap&& other)
      : roaring_(std::move(other.roaring_)),
        set_(std::move(other.set_)),
        is_roaring_(other.is_roaring_),
        has_nbit_cache_(false) {
  }

  Bitmap& operator=(Bitmap&& other) {
    if (this != std::addressof(other)) {
      roaring_ = std::move(other.roaring_);
      set_ = std::move(other.set_);
      is_roaring_ = other.is_roaring_;
      has_nbit_cache_ = false;
    }
    return *this;
  }

 public:
  // Set operations
  void Union(const Bitmap& other);

  void Exclude(const Bitmap& other);

  void Intersect(const Bitmap& other);

  void Xor(const Bitmap& other);

  void Union(const Bitmap* pother);

  void Exclude(const Bitmap* pother);

  void Intersect(const Bitmap* pother);

  void Xor(const Bitmap* pother);

  void FastUnion(std::vector<const Bitmap*>& bitmaps);

 public:
  // Data modification
  inline void Set(uint32_t id) {
    if (is_roaring_) {
      roaring_.add(id);
    } else {
      set_.insert(id);
      if (set_.size() > kSetThreshold) {
        to_roaring();
      }
    }
    has_nbit_cache_ = false;
  }

  inline void Unset(uint32_t id) {
    if (is_roaring_) {
      roaring_.remove(id);
    } else {
      set_.erase(id);
    }
    has_nbit_cache_ = false;
  }

  inline bool Isset(uint32_t id) const {
    if (is_roaring_) {
      return roaring_.contains(id);
    }

    return set_.find(id) != set_.end();
  }

  // Set [x, ..., y]
  inline void SetRange(uint32_t x, uint32_t y) {
    to_roaring();
    roaring_.addRange(x, y);
    has_nbit_cache_ = false;
  }

  inline void SetMany(const std::vector<uint32_t>& ids) {
    if (!ids.empty()) {
      to_roaring();
      roaring_.addMany(ids.size(), ids.data());
    }
    has_nbit_cache_ = false;
  }

  // Statistics, expensive to compute, use cached get_cached_nbit when possible
  inline uint32_t nbit() const {
    if (is_roaring()) {
      return roaring_.cardinality();
    }
    return static_cast<uint32_t>(set_.size());
  }

  inline uint32_t get_cached_nbit() {
    if (!has_nbit_cache_) {
      nbit_cache_ = nbit();
      has_nbit_cache_ = true;
      return nbit_cache_;
    } else {
      return nbit_cache_;
    }
  }

  inline bool empty() const {
    return roaring_.isEmpty() && set_.empty();
  }

  inline bool is_roaring() const {
    return is_roaring_;
  }

 public:
  void clear();

  // Copy content from other
  void copy(const Bitmap& other) {
    roaring_ = other.roaring_;
    set_ = other.set_;
    is_roaring_ = other.is_roaring_;
    has_nbit_cache_ = false;
  }

  void copy(const Bitmap* pother) {
    if (pother != nullptr) {
      copy(*pother);
      has_nbit_cache_ = false;
    }
  }

  // Serialization
  void SerializeToString(std::string& s);

  void ParseFromString(const std::string& s, const bool portable = true);

  // Access data
  void get_set_list(std::vector<uint32_t>& result) const;

  uint32_t get_range_list(std::vector<uint32_t>& result, uint32_t limit,
                          uint32_t offset = 0);

  size_t get_estimate_bytes();

 private:
  inline void to_roaring() {
    if (!is_roaring_) {
      for (const uint32_t id : set_) {
        roaring_.add(id);
      }
      set_.clear();
      is_roaring_ = true;
    }
  }

  inline void to_set() {
    if (is_roaring_) {
      std::vector<uint32_t> tmp;
      get_set_list(tmp);

      clear();
      set_.insert(tmp.begin(), tmp.end());
      is_roaring_ = false;
    }
  }

 private:
  roaring::Roaring roaring_;
  std::set<uint32_t> set_;
  bool is_roaring_ = false;
  uint32_t nbit_cache_ = 0;
  bool has_nbit_cache_ = false;

  static const size_t kSetThreshold = 32;
};

using BitmapPtr = std::shared_ptr<Bitmap>;

}  // namespace vectordb
