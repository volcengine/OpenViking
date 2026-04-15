// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <limits.h>
#include <math.h>
#include <cmath>
#include <algorithm>
#include <memory>
#include <mutex>
#include <vector>
#include "bitmap.h"
#include "common/io_utils.h"
#include "common/ann_utils.h"
#include "index/detail/scalar/bitmap_holder/bitmap_utils.h"

static int kRangedMapSlotSize = 10000;
static double kRangedMapSortMultiplier = 2.0;

namespace vectordb {

class RangedMap;
using RangedMapPtr = std::shared_ptr<RangedMap>;
class RangedMap2D;
class RangedMap {
 public:
  RangedMap() {
  }
  virtual ~RangedMap() = default;
  RangedMap(const RangedMap& other) = default;

  friend class RangedMap2D;

 public:
  uint32_t size() const {
    return offset_to_value_.size();
  }

  int add_offset_and_score(uint32_t offset, double value);

  int delete_offset(uint32_t offset);

  double get_score_by_offset(uint32_t offset) {
    if ((size_t)offset < offset_to_value_.size()) {
      return offset_to_value_[offset];
    }
    return -99999999.0;
  }

  BitmapPtr get_range_bitmap(bool range_out, double lower_than, bool include_le,
                             double greater_than, bool include_ge) {
    return get_range_bitmap_with_slot_data(range_out, lower_than, include_le,
                                           greater_than, include_ge);
  }

  RecallResultPtr get_topk_result(int topk, bool order_asc,
                                  offset_filter_t filter_func) {
    return get_topk_result_with_slot_data(topk, order_asc, filter_func);
  }

  RecallResultPtr get_topk_result_center1d(int topk, bool order_asc,
                                           double center1d,
                                           offset_filter_t filter_func) {
    return get_topk_result_with_slot_data_center1d(topk, order_asc, center1d,
                                                   filter_func);
  };

  RecallResultPtr get_topk_result_with_conditions(
      int topk, bool this_order_asc, offset_filter_t filter_func,
      std::vector<std::pair<RangedMapPtr, bool>> conditions) {
    return get_topk_result_with_slot_data_with_conditions(
        topk, this_order_asc, filter_func, conditions);
  };

  // Serialization
  int SerializeToStream(std::ofstream& output);
  int ParseFromStream(std::ifstream& input);

 protected:
  int slot_lower_bound_idx(double val) const {
    int l = 0, r = slots_.size();
    while (l < r) {
      int mid = l + (r - l) / 2;
      const auto& slot = slots_[mid];
      if (val > slot.right) {
        l = mid + 1;
      } else if (val <= slot.left) {
        r = mid;
      } else {
        l = mid;
        break;
      }
    }
    return l;
  }

  int slot_upper_bound_idx(double val) const {
    int l = 0, r = slots_.size();
    while (l < r) {
      int mid = l + (r - l) / 2;
      const auto& slot = slots_[mid];
      if (val >= slot.right) {
        l = mid + 1;
      } else if (val < slot.left) {
        r = mid;
      } else {
        l = mid;
        break;
      }
    }
    return l;
  }

  int find_right_slot_index(double lower_than, bool include_le = true) const {
    int slot_idx = 0;
    if (include_le) {
      slot_idx = slot_upper_bound_idx(lower_than);
      slot_idx = std::min(slot_idx, int(slots_.size()) - 1);
      if (lower_than < slots_[slot_idx].left) {
        slot_idx--;
      }
    } else {
      slot_idx = slot_lower_bound_idx(lower_than);
      slot_idx = std::min(slot_idx, int(slots_.size()) - 1);
      if (lower_than <= slots_[slot_idx].left) {
        slot_idx--;
      }
    }
    return slot_idx;
  }

  int find_left_slot_index(double greater_than, bool include_ge = true) const {
    int slot_idx = 0;
    if (include_ge) {
      slot_idx = slot_lower_bound_idx(greater_than);
    } else {
      slot_idx = slot_upper_bound_idx(greater_than);
    }
    return slot_idx;
  }

  BitmapPtr get_range_bitmap_with_slot_data(bool range_out, double lower_than,
                                            bool include_le,
                                            double greater_than,
                                            bool include_ge);

  RecallResultPtr get_topk_result_with_slot_data(int topk, bool order_asc,
                                                 offset_filter_t filter_func);

  RecallResultPtr get_topk_result_with_slot_data_center1d(
      int topk, bool order_asc, double center1d, offset_filter_t filter_func);

  RecallResultPtr sort_with_conditions(
      std::vector<uint32_t>& offsets, int topk, bool this_order_asc,
      const std::vector<std::pair<RangedMapPtr, bool>> conditions);

  RecallResultPtr get_topk_result_with_slot_data_with_conditions(
      int topk, bool this_order_asc, offset_filter_t filter_func,
      const std::vector<std::pair<RangedMapPtr, bool>> conditions);

  static uint32_t calc_slots_num(uint32_t size) {
    return (size - 1) / uint32_t(kRangedMapSlotSize) + 1;
  }

  std::vector<double> offset_to_value_;

  struct SlotMeta {
    double left;
    double right;
    BitmapPtr bitmap;
    std::vector<double> value_vec;
    std::vector<uint32_t> offset_vec;

    bool split_half_to_new_slot(SlotMeta& new_slot) {
      if (value_vec.size() < 2) {
        return false;
      }
      size_t split_idx = value_vec.size() / 2;
      new_slot.value_vec.assign(value_vec.begin() + split_idx, value_vec.end());
      new_slot.offset_vec.assign(offset_vec.begin() + split_idx,
                                 offset_vec.end());
      new_slot.bitmap->SetMany(new_slot.offset_vec);
      new_slot.left = *new_slot.value_vec.begin();
      new_slot.right = *new_slot.value_vec.rbegin();

      value_vec.resize(split_idx);
      offset_vec.resize(split_idx);
      bitmap->clear();
      bitmap->SetMany(offset_vec);
      right = *value_vec.rbegin();
      return true;
    }

    uint32_t get_lower_than_data(Bitmap* to, double lower_than,
                                 bool include_le = true) const {
      // Right bound, i.e., upper bound
      uint32_t bound_idx = 0;
      bound_idx = get_right_border(lower_than, include_le);
      for (auto iter = offset_vec.begin();
           iter < offset_vec.begin() + bound_idx; iter++) {
        to->Set(*iter);
      }
      return bound_idx;
    }

    uint32_t get_greater_than_data(Bitmap* to, double greater_than,
                                   bool include_ge = true) const {
      // Left bound, i.e., lower bound
      uint32_t bound_idx = get_left_border(greater_than, include_ge);
      for (auto iter = offset_vec.begin() + bound_idx; iter < offset_vec.end();
           iter++) {
        to->Set(*iter);
      }
      return offset_vec.size() - bound_idx;
    }

    uint32_t get_range_data(Bitmap* to, double lower_than, bool include_le,
                            double greater_than, bool include_ge) {
      uint32_t l_border = get_left_border(greater_than, include_ge);
      uint32_t r_border = get_right_border(lower_than, include_le);

      for (auto iter = offset_vec.begin() + l_border;
           iter < offset_vec.begin() + r_border; iter++) {
        to->Set(*iter);
      }
      return r_border - l_border;
    }

    uint32_t get_right_border(double lower_than, bool include_le) const {
      if (include_le) {
        const auto& u_it =
            std::upper_bound(value_vec.begin(), value_vec.end(), lower_than);
        return u_it - value_vec.begin();
      } else {
        const auto& l_it =
            std::lower_bound(value_vec.begin(), value_vec.end(), lower_than);
        return l_it - value_vec.begin();
      }
    }

    uint32_t get_left_border(double greater_than, bool include_ge) const {
      if (include_ge) {
        const auto& u_it =
            std::lower_bound(value_vec.begin(), value_vec.end(), greater_than);
        return u_it - value_vec.begin();
      } else {
        const auto& l_it =
            std::upper_bound(value_vec.begin(), value_vec.end(), greater_than);
        return l_it - value_vec.begin();
      }
    }
  };
  std::vector<SlotMeta> slots_;
};

class RangedMap2D {
 public:
  RangedMap2D(RangedMap& xmap, RangedMap& ymap) : x_(xmap), y_(ymap) {
  }
  virtual ~RangedMap2D() = default;

 public:
  inline double dist_square_to(double x, double y, uint32_t offset) const {
    double xdiff = x_.offset_to_value_[offset] - x;
    double ydiff = y_.offset_to_value_[offset] - y;
    double d2 = xdiff * xdiff + ydiff * ydiff;
    return d2;
  }
  BitmapPtr get_range2d_bitmap(double x, double y, double radius) const {
    return get_range2d_bitmap_with_slot_data(x, y, radius);
  };

 private:
  RangedMap& x_;
  RangedMap& y_;

  BitmapPtr get_range2d_bitmap_with_slot_data(double x, double y,
                                              double radius) const;
};

}  // namespace vectordb
