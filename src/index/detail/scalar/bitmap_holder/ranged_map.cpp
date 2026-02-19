// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#include "ranged_map.h"
#include <algorithm>
#include "common/io_utils.h"
#include "spdlog/spdlog.h"

namespace vectordb {

int RangedMap::SerializeToStream(std::ofstream& output) {
  write_bin(output, uint32_t(slots_.size()));
  for (const auto& slot : slots_) {
    write_bin(output, slot.left);
    write_bin(output, slot.right);
    std::string temp_data;
    slot.bitmap->SerializeToString(temp_data);
    write_str(output, temp_data);
    write_bin(output, uint32_t(slot.value_vec.size()));
    for (auto& val : slot.value_vec) {
      write_bin(output, val);
    }
    write_bin(output, uint32_t(slot.offset_vec.size()));
    for (auto& offset : slot.offset_vec) {
      write_bin(output, offset);
    }
  }
  write_bin(output, uint32_t(offset_to_value_.size()));
  for (auto& val : offset_to_value_) {
    write_bin(output, val);
  }
  return 0;
}

int RangedMap::ParseFromStream(std::ifstream& input) {
  uint32_t slot_sz = 0;
  read_bin(input, slot_sz);
  slots_.resize(slot_sz);
  for (auto& slot : slots_) {
    read_bin(input, slot.left);
    read_bin(input, slot.right);
    std::string temp_data;
    read_str(input, temp_data);
    auto bitmap = std::make_shared<Bitmap>();
    bitmap->ParseFromString(temp_data);
    slot.bitmap = bitmap;
    uint32_t value_vec_size = 0;
    read_bin(input, value_vec_size);
    slot.value_vec.resize(value_vec_size);
    for (uint32_t i = 0; i < value_vec_size; ++i) {
      read_bin(input, slot.value_vec[i]);
    }
    uint32_t offset_vec_size = 0;
    read_bin(input, offset_vec_size);
    slot.offset_vec.resize(offset_vec_size);
    for (uint32_t i = 0; i < offset_vec_size; ++i) {
      read_bin(input, slot.offset_vec[i]);
    }
  }
  uint32_t offset_to_value_size = 0;
  read_bin(input, offset_to_value_size);
  offset_to_value_.resize(offset_to_value_size);
  for (uint32_t i = 0; i < offset_to_value_size; ++i) {
    read_bin(input, offset_to_value_[i]);
  }
  return 0;
}

int RangedMap::add_offset_and_score(uint32_t offset, double value) {
  if (offset < offset_to_value_.size() &&
      !std::isnan(offset_to_value_[offset])) {
    return -1;
  }
  if (offset >= offset_to_value_.size()) {
    offset_to_value_.resize(offset + 1,
                            std::numeric_limits<double>::quiet_NaN());
  }
  offset_to_value_[offset] = value;

  if (slots_.empty()) {
    SlotMeta slot = {value, value, std::make_shared<Bitmap>(), {}, {}};
    slot.bitmap->Set(offset);
    slot.offset_vec.push_back(offset);
    slot.right = value;
    slot.value_vec.push_back(value);
    offset_to_value_[offset] = value;
    slots_.push_back(slot);
    return 0;
  }

  int slot_idx = find_right_slot_index(value, true);
  if (slot_idx == -1) {
    slot_idx = 0;
  }

  SlotMeta* add_slot = &slots_[slot_idx];
  add_slot->bitmap->Set(offset);
  const auto& vec_it = std::upper_bound(add_slot->value_vec.begin(),
                                        add_slot->value_vec.end(), value);
  size_t vec_idx = vec_it - add_slot->value_vec.begin();
  // This has significant memory movement overhead
  add_slot->value_vec.insert(vec_it, value);
  add_slot->offset_vec.insert(add_slot->offset_vec.begin() + vec_idx, offset);

  add_slot->left = std::min(value, add_slot->left);
  add_slot->right = std::max(value, add_slot->right);

  // Split into two nodes when exceeding threshold due to uneven insertion
  if (add_slot->value_vec.size() >
      static_cast<size_t>(kRangedMapSlotSize * 2)) {
    // size_t old_val_size = add_slot->value_vec.size();
    // size_t old_slot_size = slots_.size();
    SlotMeta new_slot{0.0f, 0.0f, std::make_shared<Bitmap>(), {}, {}};
    slots_.insert(slots_.begin() + slot_idx + 1, new_slot);
    add_slot = &slots_[slot_idx];
    add_slot->split_half_to_new_slot(slots_[slot_idx + 1]);
  }

  return 0;
}

int RangedMap::delete_offset(uint32_t offset) {
  if (slots_.empty()) {
    return -1;
  }
  if (offset >= offset_to_value_.size() ||
      std::isnan(offset_to_value_[offset])) {
    SPDLOG_WARN(
        "RangedMap[{}]::delete_offset_and_score cannot delete, offset {} not exist, offset_to_value_.size() {}",
        static_cast<void*>(this), offset, offset_to_value_.size());
    return -1;
  }

  auto value = offset_to_value_[offset];
  offset_to_value_[offset] = std::numeric_limits<double>::quiet_NaN();

  auto slot_idx = size_t(find_left_slot_index(value, true));
  while (slot_idx < slots_.size() && !slots_[slot_idx].bitmap->Isset(offset)) {
    slot_idx++;
  }

  if (slot_idx >= slots_.size()) {
    SPDLOG_ERROR(
        "RangedMap::delete_offset_and_score error, cannot find slot for value {} from offset {}",
        value, offset);
    return -1;
  }

  auto& delete_slot = slots_[slot_idx];
  delete_slot.bitmap->Unset(offset);

  const auto& vec_it = std::lower_bound(delete_slot.value_vec.begin(),
                                        delete_slot.value_vec.end(), value);
  auto offset_it =
      delete_slot.offset_vec.begin() + (vec_it - delete_slot.value_vec.begin());

  while (offset_it != delete_slot.offset_vec.end() && *offset_it != offset) {
    offset_it++;
  }

  if (offset_it == delete_slot.offset_vec.end()) {
    return -1;
  }

  auto vec_idx = offset_it - delete_slot.offset_vec.begin();
  delete_slot.offset_vec.erase(offset_it);
  delete_slot.value_vec.erase(delete_slot.value_vec.begin() + vec_idx);

  if (!delete_slot.value_vec.empty()) {
    delete_slot.left = *delete_slot.value_vec.begin();
    delete_slot.right = *delete_slot.value_vec.rbegin();
  } else {
    slots_.erase(slots_.begin() + slot_idx);
  }
  return 0;
}

BitmapPtr RangedMap::get_range_bitmap_with_slot_data(bool range_out,
                                                     double lower_than,
                                                     bool include_le,
                                                     double greater_than,
                                                     bool include_ge) {
  if (slots_.empty()) {
    return nullptr;
  }
  auto temp = std::make_shared<Bitmap>();
  Bitmap* temp_p = temp.get();
  // uint32_t total = offset_to_value_.size();

  if (range_out && lower_than < greater_than) {
    std::swap(lower_than, greater_than);
    // Swap include_le and include_ge
    bool temp_le = include_le;
    include_le = include_ge;
    include_ge = temp_le;
  }

  int r_index = find_right_slot_index(lower_than, include_le);
  int l_index = find_left_slot_index(greater_than, include_ge);

  uint32_t cnt = 0;
  if (!range_out) {
    for (int i = l_index + 1; i < r_index; i++) {
      temp_p->Union(slots_[i].bitmap.get());
      cnt += slots_[i].offset_vec.size();
    }

    if (r_index != -1 && l_index != static_cast<int>(slots_.size())) {
      if (l_index < r_index) {
        cnt +=
            slots_[r_index].get_lower_than_data(temp_p, lower_than, include_le);
        cnt += slots_[l_index].get_greater_than_data(temp_p, greater_than,
                                                     include_ge);
      } else if (l_index == r_index) {
        cnt += slots_[r_index].get_range_data(temp_p, lower_than, include_le,
                                              greater_than, include_ge);
      }
    }
  } else {
    for (int i = 0; i < l_index; i++) {
      temp_p->Union(slots_[i].bitmap.get());
      cnt += slots_[i].offset_vec.size();
    }
    if (l_index != static_cast<int>(slots_.size()) && l_index != -1) {
      cnt += slots_[l_index].get_lower_than_data(temp_p, greater_than,
                                                 !include_ge);
    }

    for (int i = r_index + 1; i < static_cast<int>(slots_.size()); i++) {
      temp_p->Union(slots_[i].bitmap.get());
      cnt += slots_[i].offset_vec.size();
    }
    if (r_index != -1 && r_index != static_cast<int>(slots_.size())) {
      cnt += slots_[r_index].get_greater_than_data(temp_p, lower_than,
                                                   !include_le);
    }
  }

  if (cnt <= 0) {
    return nullptr;
  }

  return temp;
}

RecallResultPtr RangedMap::get_topk_result_with_slot_data(
    int topk, bool order_asc, offset_filter_t filter_func) {
  std::vector<uint32_t> temp_offsets;
  std::vector<float> temp_scores;
  temp_offsets.reserve(topk);
  temp_scores.reserve(topk);
  int cnt = 0;
  const bool has_filter = (bool)(filter_func);
  if (order_asc) {
    for (auto slot_idx = 0; slot_idx < (int)slots_.size() && cnt < topk;
         slot_idx++) {
      for (auto offset_idx = 0;
           offset_idx < (int)slots_[slot_idx].offset_vec.size(); offset_idx++) {
        if (has_filter &&
            filter_func(slots_[slot_idx].offset_vec[offset_idx])) {
          continue;
        }

        temp_scores.emplace_back(slots_[slot_idx].value_vec[offset_idx]);
        temp_offsets.emplace_back(slots_[slot_idx].offset_vec[offset_idx]);
        if (++cnt >= topk)
          break;
      }
    }
  } else {
    for (int slot_idx = (int)slots_.size() - 1; slot_idx >= 0 && cnt < topk;
         slot_idx--) {
      for (int offset_idx = (int)slots_[slot_idx].offset_vec.size() - 1;
           offset_idx >= 0; offset_idx--) {
        if (has_filter &&
            filter_func(slots_[slot_idx].offset_vec[offset_idx])) {
          continue;
        }
        temp_scores.emplace_back(slots_[slot_idx].value_vec[offset_idx]);
        temp_offsets.emplace_back(slots_[slot_idx].offset_vec[offset_idx]);
        if (++cnt >= topk)
          break;
      }
    }
  }
  RecallResultPtr res_ptr = std::make_shared<RecallResult>();
  if (res_ptr->swap_offsets_vec(temp_scores, temp_offsets) != 0) {
    return nullptr;
  }
  return res_ptr;
}

RecallResultPtr RangedMap::get_topk_result_with_slot_data_center1d(
    int topk, bool order_asc, double center1d, offset_filter_t filter_func) {
  // only support order_asc == true now.
  if (slots_.empty()) {
    return std::make_shared<RecallResult>();
  }
  std::vector<uint32_t> temp_offsets;
  std::vector<float> temp_scores;
  temp_offsets.reserve(topk);
  temp_scores.reserve(topk);
  int cnt = 0;
  const bool has_filter = (bool)(filter_func);
  // bi search to find the lower bound and upper bound of center1d
  int slot_l, offset_l, slot_r, offset_r;
  if (slots_[0].value_vec[0] > center1d) {
    slot_l = -1, offset_l = 0, slot_r = 0, offset_r = 0;
  } else if (slots_.back().value_vec.back() < center1d) {
    slot_l = (int)slots_.size(), offset_l = 0, slot_r = (int)slots_.size(),
    offset_r = 0;
  } else {
    for (slot_l = 0; slot_l < (int)slots_.size(); slot_l++) {
      if (slots_[slot_l].value_vec.back() >= center1d) {
        break;
      }
    }
    offset_l = std::lower_bound(slots_[slot_l].value_vec.begin(),
                                slots_[slot_l].value_vec.end(), center1d) -
               slots_[slot_l].value_vec.begin();
    for (slot_r = (int)slots_.size() - 1; slot_r >= 0; slot_r--) {
      if (slots_[slot_r].value_vec.back() <= center1d) {
        break;
      }
    }
    slot_r++;
    offset_r = std::upper_bound(slots_[slot_r].value_vec.begin(),
                                slots_[slot_r].value_vec.end(), center1d) -
               slots_[slot_r].value_vec.begin();
  }
  // add values between lower bound and upper bound
  if (slot_l != -1) {
    for (int slot_i = slot_l, offset_i = offset_l;
         (slot_i < slot_r || (slot_i == slot_r && offset_i < offset_r)) &&
         cnt < topk;) {
      if (!has_filter || !filter_func(slots_[slot_i].offset_vec[offset_i])) {
        temp_scores.emplace_back(slots_[slot_i].value_vec[offset_i]);
        temp_offsets.emplace_back(slots_[slot_i].offset_vec[offset_i]);
        cnt++;
      }
      if (++offset_i == (int)slots_[slot_i].offset_vec.size()) {
        slot_i++, offset_i = 0;
      }
    }
    if (--offset_l == -1 && --slot_l != -1) {
      offset_l = (int)slots_[slot_l].offset_vec.size() - 1;
    }
  }

  // add values beyond lower bound and upper bound
  while (cnt < topk and (slot_l != -1 || slot_r != (int)slots_.size())) {
    if (has_filter) {
      while (slot_l != -1 && filter_func(slots_[slot_l].offset_vec[offset_l])) {
        if (--offset_l == -1 && --slot_l != -1) {
          offset_l = (int)slots_[slot_l].offset_vec.size() - 1;
        }
      }
      while (slot_r != (int)slots_.size() &&
             filter_func(slots_[slot_r].offset_vec[offset_r])) {
        if (++offset_r == (int)slots_[slot_r].offset_vec.size()) {
          slot_r++, offset_r = 0;
        }
      }
    }
    if (slot_l == -1 && slot_r == (int)slots_.size()) {
      break;
    } else if (slot_l != -1 && slot_r != (int)slots_.size()) {
      if (std::abs(center1d - slots_[slot_l].value_vec[offset_l]) <=
          std::abs(center1d - slots_[slot_r].value_vec[offset_r])) {
        temp_scores.emplace_back(slots_[slot_l].value_vec[offset_l]);
        temp_offsets.emplace_back(slots_[slot_l].offset_vec[offset_l]);
        if (--offset_l == -1 && --slot_l != -1) {
          offset_l = (int)slots_[slot_l].offset_vec.size() - 1;
        }
      } else {
        temp_scores.emplace_back(slots_[slot_r].value_vec[offset_r]);
        temp_offsets.emplace_back(slots_[slot_r].offset_vec[offset_r]);
        if (++offset_r == (int)slots_[slot_r].offset_vec.size()) {
          slot_r++, offset_r = 0;
        }
      }
    } else if (slot_l != -1) {
      temp_scores.emplace_back(slots_[slot_l].value_vec[offset_l]);
      temp_offsets.emplace_back(slots_[slot_l].offset_vec[offset_l]);
      if (--offset_l == -1 && --slot_l != -1) {
        offset_l = (int)slots_[slot_l].offset_vec.size() - 1;
      }
    } else {
      temp_scores.emplace_back(slots_[slot_r].value_vec[offset_r]);
      temp_offsets.emplace_back(slots_[slot_r].offset_vec[offset_r]);
      if (++offset_r == (int)slots_[slot_r].offset_vec.size()) {
        slot_r++, offset_r = 0;
      }
    }
    cnt++;
  }

  RecallResultPtr res_ptr = std::make_shared<RecallResult>();
  if (res_ptr->swap_offsets_vec(temp_scores, temp_offsets) != 0) {
    return nullptr;
  }
  return res_ptr;
}

// with multi conditions
RecallResultPtr RangedMap::sort_with_conditions(
    std::vector<uint32_t>& offsets, int topk, bool this_order_asc,
    const std::vector<std::pair<RangedMapPtr, bool>> conditions) {
  const auto cond_func = [&](uint32_t& idx_l, uint32_t& idx_r) -> bool {
    double value_l = get_score_by_offset(idx_l);
    double value_r = get_score_by_offset(idx_r);
    if (value_l != value_r)
      return (value_l > value_r) ^ this_order_asc;
    for (const std::pair<RangedMapPtr, bool>& condition : conditions) {
      value_l = condition.first->get_score_by_offset(idx_l);
      value_r = condition.first->get_score_by_offset(idx_r);
      if (value_l != value_r)
        return (value_l > value_r) ^ condition.second;
    }
    return false;
  };
  std::sort(offsets.begin(), offsets.end(), cond_func);
  offsets.resize(std::min(offsets.size(), (size_t)topk));

  std::vector<float> scores;
  scores.reserve(offsets.size());
  for (uint32_t& temp_offset : offsets) {
    scores.emplace_back(get_score_by_offset(temp_offset));
  }

  RecallResultPtr res_ptr = std::make_shared<RecallResult>();
  if (res_ptr->swap_offsets_vec(scores, offsets) != 0) {
    return nullptr;
  }
  return res_ptr;
}

RecallResultPtr RangedMap::get_topk_result_with_slot_data_with_conditions(
    int topk, bool this_order_asc, offset_filter_t filter_func,
    const std::vector<std::pair<RangedMapPtr, bool>> conditions) {
  const int max_size = topk * kRangedMapSortMultiplier;

  std::vector<uint32_t> temp_offsets;
  temp_offsets.reserve(max_size);
  const bool has_filter = (bool)(filter_func);
  int cnt = 0;
  double last_score = 0.0;

  int slot_from, slot_to, step;
  if (this_order_asc) {
    slot_from = 0, slot_to = (int)slots_.size(), step = 1;
  } else {
    slot_from = (int)slots_.size() - 1, slot_to = -1, step = -1;
  }
  for (int slot_idx = 0; slot_idx < (int)slots_.size() && cnt < max_size;
       slot_idx++) {
    int offset_from, offset_to;
    if (this_order_asc) {
      offset_from = 0, offset_to = (int)slots_[slot_idx].offset_vec.size();
    } else {
      offset_from = (int)slots_[slot_idx].offset_vec.size() - 1, offset_to = -1;
    }
    for (int offset_idx = offset_from; offset_idx != offset_to;
         offset_idx += step) {
      if (has_filter && filter_func(slots_[slot_idx].offset_vec[offset_idx])) {
        continue;
      }
      double this_score = slots_[slot_idx].value_vec[offset_idx];
      temp_offsets.emplace_back(slots_[slot_idx].offset_vec[offset_idx]);
      if (++cnt >= max_size) {
        break;
      } else if (cnt > std::max(topk, 1) && last_score != this_score)
        break;
      last_score = this_score;
    }
  }
  return sort_with_conditions(temp_offsets, topk, this_order_asc, conditions);
}

// RangedMap2D implementation

BitmapPtr RangedMap2D::get_range2d_bitmap_with_slot_data(double x, double y,
                                                         double radius) const {
  if (radius <= 0.0) {
    return nullptr;
  }
  BitmapPtr temp = x_.get_range_bitmap_with_slot_data(false, x + radius, true,
                                                      x - radius, true);
  if (temp == nullptr || temp->empty())
    return nullptr;

  BitmapPtr y_temp = y_.get_range_bitmap_with_slot_data(false, y + radius, true,
                                                        y - radius, true);
  if (y_temp == nullptr || y_temp->empty())
    return nullptr;

  temp->Intersect(y_temp.get());

  Bitmap* temp_p = temp.get();
  std::vector<uint32_t> offsets;
  temp_p->get_set_list(offsets);
  const double dist_square_max = radius * radius;
  for (uint32_t offset : offsets) {
    double d2 = dist_square_to(x, y, offset);
    if (d2 > dist_square_max) {
      temp_p->Unset(offset);
    }
  }
  if (temp_p->get_cached_nbit() <= 0) {
    return nullptr;
  }
  return temp;
}

}  // namespace vectordb
