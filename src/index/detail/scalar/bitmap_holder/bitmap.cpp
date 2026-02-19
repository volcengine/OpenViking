// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#include "bitmap.h"

#include <algorithm>
#include <random>
#include <croaring/roaring.c>

namespace vectordb {

void Bitmap::clear() {
  roaring::Roaring().swap(roaring_);
  set_.clear();
  is_roaring_ = false;
  has_nbit_cache_ = false;
}

void Bitmap::Union(const Bitmap& other) {
  if (other.is_roaring_) {
    to_roaring();
    roaring_ |= other.roaring_;
  } else {
    for (const uint32_t id : other.set_) {
      Set(id);
    }
  }
  has_nbit_cache_ = false;
}

void Bitmap::FastUnion(std::vector<const Bitmap*>& bitmaps) {
  if (bitmaps.empty()) {
    return;
  }

  if (bitmaps.size() == 1) {
    Union(bitmaps[0]);
    return;
  }

  to_roaring();
  bool lazy = false;
  for (size_t i = 0; i < bitmaps.size();) {
    const Bitmap* bitmap = bitmaps[i];
    if (!bitmap->is_roaring()) {
      ++i;
      continue;
    }

    lazy = true;
    roaring_bitmap_lazy_or_inplace(&roaring_.roaring,
                                   &bitmaps[i]->roaring_.roaring,
                                   LAZY_OR_BITSET_CONVERSION);
    std::swap(bitmaps[i], bitmaps.back());
    bitmaps.pop_back();
  }

  if (lazy) {
    roaring_bitmap_repair_after_lazy(&roaring_.roaring);
  }

  for (size_t i = 0; i < bitmaps.size(); ++i) {
    Union(bitmaps[i]);
  }
  has_nbit_cache_ = false;
}

void Bitmap::Exclude(const Bitmap& other) {
  if (is_roaring_ && other.is_roaring_) {
    roaring_ -= other.roaring_;
    return;
  }

  if (!other.is_roaring_) {
    // self: roaring or set
    // other: set
    for (const uint32_t id : other.set_) {
      Unset(id);
    }
  } else {
    // self: set
    // other: roaring
    for (auto iter = set_.begin(); iter != set_.end();) {
      if (other.Isset(*iter)) {
        iter = set_.erase(iter);
      } else {
        ++iter;
      }
    }
  }
  has_nbit_cache_ = false;
}

void Bitmap::Intersect(const Bitmap& other) {
  if (is_roaring_ && other.is_roaring_) {
    roaring_ &= other.roaring_;
    return;
  }

  if (is_roaring_) {
    // self: roaring
    // other: set
    std::set<uint32_t> s;
    for (const uint32_t id : other.set_) {
      if (Isset(id)) {
        s.insert(id);
      }
    }

    clear();
    set_ = std::move(s);
    is_roaring_ = false;
  } else {
    // self: set
    // other: roaring or set
    for (auto iter = set_.begin(); iter != set_.end();) {
      if (!other.Isset(*iter)) {
        iter = set_.erase(iter);
      } else {
        ++iter;
      }
    }
  }
  has_nbit_cache_ = false;
}

void Bitmap::Xor(const Bitmap& other) {
  // Currently only used by DPA, simple implementation
  to_roaring();

  if (other.is_roaring_) {
    roaring_ ^= other.roaring_;
  } else {
    roaring::Roaring r;
    for (const uint32_t id : other.set_) {
      r.add(id);
    }
    roaring_ ^= r;
  }
  has_nbit_cache_ = false;
}

void Bitmap::Union(const Bitmap* pother) {
  if (pother != nullptr) {
    Union(*pother);
    has_nbit_cache_ = false;
  }
}

void Bitmap::Exclude(const Bitmap* pother) {
  if (pother != nullptr) {
    Exclude(*pother);
    has_nbit_cache_ = false;
  }
}

void Bitmap::Intersect(const Bitmap* pother) {
  if (pother != nullptr) {
    Intersect(*pother);
    has_nbit_cache_ = false;
  }
}

void Bitmap::Xor(const Bitmap* pother) {
  if (pother != nullptr) {
    Xor(*pother);
    has_nbit_cache_ = false;
  }
}

size_t Bitmap::get_estimate_bytes() {
  size_t estimate_bytes = roaring_.getSizeInBytes();
  estimate_bytes += sizeof(Bitmap);
  // use roaring or set
  estimate_bytes += set_.size() * (sizeof(uint32_t) + 40);
  return estimate_bytes;
}

void Bitmap::SerializeToString(std::string& s) {
  to_roaring();

  s.clear();
  uint32_t max_bytes = roaring_.getSizeInBytes();
  std::unique_ptr<char[]> write_buf(new char[max_bytes + 1]);
  uint32_t write_bytes = roaring_.write(write_buf.get());
  s.assign(write_buf.get(), write_bytes);
}

void Bitmap::ParseFromString(const std::string& s, const bool portable) {
  clear();
  is_roaring_ = true;

  roaring_ = roaring::Roaring::read(s.c_str(), portable);
  if (roaring_.cardinality() <= kSetThreshold) {
    to_set();
  }
  has_nbit_cache_ = false;
}

void Bitmap::get_set_list(std::vector<uint32_t>& result) const {
  result.clear();
  if (is_roaring_) {
    result.resize(nbit(), 0);
    roaring_.toUint32Array(result.data());
  } else {
    result.insert(result.end(), set_.begin(), set_.end());
  }
}

uint32_t Bitmap::get_range_list(std::vector<uint32_t>& result, uint32_t limit,
                                uint32_t offset) {
  uint32_t max_num = get_cached_nbit();
  uint32_t real_limit = std::min<uint32_t>(max_num - offset, limit);
  if (max_num <= offset || real_limit <= 0) {
    return 0;
  }

  if (result.size() != (size_t)real_limit) {
    result.resize(real_limit, 0);
  }
  if (is_roaring_) {
    roaring_.rangeUint32Array(result.data(), offset, real_limit);
  } else {
    auto iter = set_.begin();
    std::advance(iter, offset);
    std::copy_n(iter, real_limit, result.begin());
  }
  return static_cast<uint32_t>(result.size());
}

}  // namespace vectordb
