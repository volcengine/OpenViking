// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "spdlog/spdlog.h"
#include "common/json_utils.h"
#include "common/string_utils.h"

#include <algorithm>
#include <vector>
#include <fstream>

#include "index/detail/scalar/bitmap_holder/bitmap.h"
#include "index/detail/scalar/bitmap_holder/bitmap_utils.h"
#include "index/detail/scalar/bitmap_holder/ranged_map.h"
#include "index/detail/scalar/bitmap_holder/dir_index.h"

namespace vectordb {
// BitmapGroupBase for a group of related bitmaps
// Container for different bitmaps built from different enums of the same field
// Data hierarchy: Model -> Context -> Group -> Bitmap

using LABEL_U64_OFFSET_CONVERT_FUNC =
    std::function<bool(const std::vector<uint64_t>&, std::vector<uint32_t>&)>;

class BitmapGroupBase {
 public:
  const static int kBitmapGroupUnknown = 0;
  const static int kBitmapGroupBitmaps = 1;
  const static int kBitmapGroupRangedMap = 2;
  const static int kBitmapGroupBothBitmapsAndRange = 3;
  const static int kBitmapGroupDir = 4;

  BitmapGroupBase(const std::string group_set_name,
                  const std::string group_name,
                  const int type_id = kBitmapGroupUnknown);
  virtual ~BitmapGroupBase();
  bool exist_bitmap(const std::string& key);
  const Bitmap* get_bitmap(const std::string& key);
  Bitmap* get_editable_bitmap(const std::string& key);
  BitmapPtr get_bitmap_copy(const std::string& key);
  RangedMap* get_editable_rangedmap();
  BitmapPtr get_bitmap_in_range(bool range_out, double lower_than,
                                bool include_le, double greater_than,
                                bool include_ge);
  RecallResultPtr get_topk_result(int topk, bool order_asc,
                                  offset_filter_t filter);
  RecallResultPtr get_topk_result_center1d(int topk, bool order_asc,
                                           double center1d,
                                           offset_filter_t filter);
  RecallResultPtr get_topk_result_with_conditions(
      int topk, bool this_order_asc, offset_filter_t filter,
      std::vector<std::pair<RangedMapPtr, bool>> conditions);

  virtual bool is_valid();
  const std::string& group_name() {
    return group_name_;
  }
  virtual void clear();

  int get_type_id() const {
    if (type_id_ != kBitmapGroupUnknown) {
      return type_id_;
    }
    if (dir_index_) {
      return kBitmapGroupDir;
    }
    if (!bitmap_group_.empty() && rangedmap_ptr_) {
      return kBitmapGroupBothBitmapsAndRange;
    }
    if (!bitmap_group_.empty()) {
      return kBitmapGroupBitmaps;
    }
    if (rangedmap_ptr_) {
      return kBitmapGroupRangedMap;
    }
    return kBitmapGroupUnknown;
  }

  void set_type_id(const int type_id) {
    type_id_ = type_id;
  }

  RangedMapPtr get_rangedmap_ptr() {
    return rangedmap_ptr_;
  }

  DirIndexPtr get_dir_index() {
    return dir_index_;
  }

  int count_field_enums(std::map<std::string, uint32_t>& enum_count,
                        std::map<std::string, const BitmapPtr>& enum_bitmaps,
                        BitmapPtr valid_bitmap);
  int count_field_enums(std::map<std::string, uint32_t>& enum_count,
                        std::map<std::string, const BitmapPtr>& enum_bitmaps);

  // Get union of bitmaps whose keys match the prefix
  BitmapPtr get_bitmap_by_prefix(const std::string& prefix);

  // Get union of bitmaps whose keys contain the substring
  BitmapPtr get_bitmap_by_contains(const std::string& substring);

  // Get union of bitmaps whose keys match the regex pattern
  BitmapPtr get_bitmap_by_regex(const std::string& pattern);

 protected:
  virtual void _clear() {
  }

 protected:
  std::string group_set_name_;  // From loading, the owning group_set
  std::string group_name_;
  std::map<std::string, BitmapPtr> bitmap_group_;
  RangedMapPtr rangedmap_ptr_;
  DirIndexPtr dir_index_;
  int type_id_;
};

// Inverted index or continuous value index group for a single field
class FieldBitmapGroup : public BitmapGroupBase {
 public:
  FieldBitmapGroup(const std::string group_set_name,
                   const std::string field_name,
                   const int type_id = kBitmapGroupUnknown)
      : BitmapGroupBase(group_set_name, field_name, type_id) {
    element_size_ = 0;
  };
  virtual ~FieldBitmapGroup() {
  }

  virtual int add_field_data(const std::string& field_str, int offset) {
    auto found = field_str.find(';');
    if (found != std::string::npos) {
      std::vector<std::string> keys;
      split(keys, field_str, ";");
      for (auto& key_i : keys) {
        const std::string norm_key = dir_index_ ? normalize_path_key(key_i) : key_i;
        if (!exist_bitmap(norm_key)) {
          if (dir_index_) {
            dir_index_->add_key(norm_key);
          }
        }

        Bitmap* temp_p = get_editable_bitmap(norm_key);
        if (temp_p) {
          temp_p->Set(offset);
        }
      }

    } else {
      const std::string norm_key = dir_index_ ? normalize_path_key(field_str) : field_str;
      if (!exist_bitmap(norm_key)) {
        if (dir_index_) {
          dir_index_->add_key(norm_key);
        }
      }
      Bitmap* temp_p = get_editable_bitmap(norm_key);
      if (temp_p) {
        temp_p->Set(offset);
      }
    }
    element_size_ = std::max(static_cast<size_t>(offset + 1), element_size_);
    return 0;
  }

  virtual int add_field_data(int64_t field_id, int offset) {
    Bitmap* temp_p = get_editable_bitmap(std::to_string(field_id));
    if (temp_p) {
      temp_p->Set(offset);
    }
    element_size_ = std::max(static_cast<size_t>(offset + 1), element_size_);
    return 0;
  }

  virtual int add_field_data(double field_dbl, int offset) {
    RangedMap* temp_p = get_editable_rangedmap();
    if (!temp_p) {
      return -1;
    }
    int ret = temp_p->add_offset_and_score(offset, field_dbl);
    if (ret != 0) {
      return ret;
    }
    element_size_ = std::max(static_cast<size_t>(offset + 1), element_size_);
    return 0;
  };

  virtual int add_field_data(float field_ff, int offset) {
    RangedMap* temp_p = get_editable_rangedmap();
    if (!temp_p) {
      return -1;
    }
    int ret = temp_p->add_offset_and_score(offset, (double)field_ff);
    if (ret != 0) {
      return ret;
    }
    element_size_ = std::max(static_cast<size_t>(offset + 1), element_size_);
    return 0;
  };

  virtual int delete_field_data(const std::string& field_str, int offset) {
    if (static_cast<size_t>(offset) >= element_size_) {
      return -1;
    }

    auto found = field_str.find(';');
    if (found != std::string::npos) {
      std::vector<std::string> keys;
      split(keys, field_str, ";");
      for (auto& key_i : keys) {
        const std::string norm_key = dir_index_ ? normalize_path_key(key_i) : key_i;
        Bitmap* temp_p = get_editable_bitmap(norm_key);
        if (temp_p) {
          temp_p->Unset(offset);
        }
      }
    } else {
      const std::string norm_key = dir_index_ ? normalize_path_key(field_str) : field_str;
      Bitmap* temp_p = get_editable_bitmap(norm_key);
      if (temp_p) {
        temp_p->Unset(offset);
      }
    }
    return 0;
  }

  virtual int delete_field_data(int64_t field_id, int offset) {
    if (static_cast<size_t>(offset) >= element_size_) {
      SPDLOG_ERROR(
          "delete_field_data failed, field_id %ld, offset %d is invalid when element_size is %lu\n",
          field_id, offset, element_size_);
      return -1;
    }

    Bitmap* temp_p = get_editable_bitmap(std::to_string(field_id));
    if (temp_p) {
      temp_p->Unset(offset);
    }
    return 0;
  }

  virtual int delete_field_data(double field_dbl, int offset) {
    if (static_cast<size_t>(offset) >= element_size_) {
      SPDLOG_ERROR(
          "delete_field_data failed, field_id %lf, offset %d is invalid when element_size is %lu",
          field_dbl, offset, element_size_);
      return -1;
    }

    RangedMap* temp_p = get_editable_rangedmap();
    if (!temp_p) {
      return -1;
    }
    int ret = temp_p->delete_offset(offset);
    if (ret != 0) {
      SPDLOG_WARN("delete_field_data failed, ret %d", ret);
      return ret;
    }
    return 0;
  };

  virtual int serialize_to_stream(std::ofstream& output);
  virtual int parse_from_stream(std::ifstream& input);

  size_t element_size() {
    return element_size_;
  }

 protected:
  virtual void _clear() {
    element_size_ = 0;
  }

 private:
  static std::string normalize_path_key(const std::string& key) {
    if (key.empty() || key[0] == '/') {
      return key;
    }
    return "/" + key;
  }

  size_t element_size_;
};

using FieldBitmapGroupPtr = std::shared_ptr<FieldBitmapGroup>;

class FieldBitmapGroupSet;
using FieldBitmapGroupSetPtr = std::shared_ptr<FieldBitmapGroupSet>;

// Collection of all field inverted index groups, encapsulates bitmap filter computation
class FieldBitmapGroupSet {
 public:
  explicit FieldBitmapGroupSet(std::string grp_set_name = std::string{})
      : group_set_name_(std::move(grp_set_name)) {
    element_size_ = 0;
  }
  virtual ~FieldBitmapGroupSet() {
  }

  virtual int add_field_group(FieldBitmapGroupPtr field_bitmap_group);

  virtual int add_field_data(
      const std::unordered_map<std::string, std::string>& str_kv_map,
      int offset) {
    int ret = 0;
    for (auto iter : str_kv_map) {
      if (field_names_.find(iter.first) == field_names_.end()) {
        continue;
      }
      auto field_group = find_field_group(iter.first);
      if (field_group == nullptr) {
        SPDLOG_WARN(
            "add_field_data failed, get file group for %s to group_set %s failed\n",
            iter.first.c_str(), group_set_name_.c_str());
        continue;
      }
      ret = field_group->add_field_data(iter.second, offset);
      if (ret != 0) {
        SPDLOG_WARN(
            "add_field_data failed, add offset %d to bitmap %s:%s failed, got ret %d\n",
            offset, iter.first.c_str(), iter.second.c_str(), ret);
        break;
      }
    }
    element_size_ = std::max(element_size_, static_cast<size_t>(offset + 1));
    return ret;
  }

  virtual int add_field_data(
      const std::unordered_map<std::string, int64_t>& id_kv_map, int offset) {
    int ret = 0;
    for (auto iter : id_kv_map) {
      if (field_names_.find(iter.first) == field_names_.end()) {
        continue;
      }
      auto field_group = find_field_group(iter.first);
      if (field_group == nullptr) {
        continue;
      }
      ret = field_group->add_field_data(iter.second, offset);
    }
    element_size_ = std::max(element_size_, static_cast<size_t>(offset + 1));
    return ret;
  }

  virtual int add_field_data(
      const std::unordered_map<std::string, double>& double_kv_map,
      int offset) {
    int ret = 0;
    for (auto iter : double_kv_map) {
      if (field_names_.find(iter.first) == field_names_.end()) {
        continue;
      }
      auto field_group = find_field_group(iter.first);
      if (field_group == nullptr) {
        continue;
      }
      ret = field_group->add_field_data(iter.second, offset);
    }
    element_size_ = std::max(element_size_, static_cast<size_t>(offset + 1));
    return ret;
  }

  virtual int add_field_data(
      const std::unordered_map<std::string, float>& float_kv_map, int offset) {
    int ret = 0;
    for (auto iter : float_kv_map) {
      if (field_names_.find(iter.first) == field_names_.end()) {
        continue;
      }
      auto field_group = find_field_group(iter.first);
      if (field_group == nullptr) {
        SPDLOG_WARN(
            "add_field_data failed, get file group for %s in group_set %s failed",
            iter.first.c_str(), group_set_name_.c_str());
        continue;
      }
      ret = field_group->add_field_data(iter.second, offset);
      if (ret != 0) {
        SPDLOG_WARN(
            "add_field_data failed, add offset %d to ranged bitmap %s with %f failed, got ret %d",
            offset, iter.first.c_str(), iter.second, ret);
      }
    }
    element_size_ = std::max(element_size_, static_cast<size_t>(offset + 1));
    return ret;
  }

  virtual int delete_field_data(
      const std::unordered_map<std::string, std::string>& str_kv_map,
      int offset) {
    if (static_cast<size_t>(offset) >= element_size_) {
      SPDLOG_WARN(
          "delete_field_data failed, offset %d is invalid when element_size is %lu",
          offset, element_size_);
      return -1;
    }

    int ret = 0;
    for (auto iter : str_kv_map) {
      auto field_group = find_field_group(iter.first);
      if (field_group == nullptr) {
        continue;
      }
      ret = field_group->delete_field_data(iter.second, offset);
      if (ret != 0) {
        SPDLOG_WARN(
            "delete_field_data failed, delete offset %d in bitmap %s:%s failed, got ret %d",
            offset, iter.first.c_str(), iter.second.c_str(), ret);
      }
    }
    return ret;
  }

  virtual int delete_field_data(
      const std::unordered_map<std::string, int64_t>& id_kv_map, int offset) {
    if (static_cast<size_t>(offset) >= element_size_) {
      return -1;
    }

    int ret = 0;
    for (auto iter : id_kv_map) {
      auto field_group = find_field_group(iter.first);
      if (field_group == nullptr) {
        continue;
      }
      ret = field_group->delete_field_data(iter.second, offset);
    }
    return ret;
  }

  virtual int delete_field_data(
      const std::unordered_map<std::string, double>& double_kv_map,
      int offset) {
    if (static_cast<size_t>(offset) >= element_size_) {
      SPDLOG_ERROR(
          "delete_field_data failed, offset %d is invalid when element_size is %lu",
          offset, element_size_);
      return -1;
    }

    int ret = 0;
    for (auto iter : double_kv_map) {
      auto field_group = find_field_group(iter.first);
      if (field_group == nullptr) {
        continue;
      }
      ret = field_group->delete_field_data(iter.second, offset);
      if (ret != 0) {
        SPDLOG_ERROR(
            "delete_field_data failed, delete offset %d in ranged bitmap %s with %f failed, got ret %d \n",
            offset, iter.first.c_str(), iter.second, ret);
      }
    }
    return ret;
  }

  FieldBitmapGroupPtr find_field_group(const std::string& field_name);

  size_t element_size() {
    return element_size_;
  }

  const Bitmap* get_bitmap(const std::string& field, const std::string key) {
    return get_editable_bitmap(field, key);
  }

  Bitmap* get_editable_bitmap(const std::string& field, const std::string key) {
    auto itr = field_bitmap_groups_map_.find(field);
    if (itr == field_bitmap_groups_map_.end()) {
      return nullptr;
    }
    return itr->second->get_editable_bitmap(key);
  }

  BitmapPtr make_range_copy(bool range_out, const std::string& field,
                            double lower_than, bool include_le,
                            double greater_than, bool include_ge) {
    auto itr = field_bitmap_groups_map_.find(field);
    if (itr == field_bitmap_groups_map_.end()) {
      SPDLOG_ERROR("make_range_copy failed, cannot find {} in {}\n",
                   field, field_bitmap_groups_map_.size());
      return nullptr;
    }
    return itr->second->get_bitmap_in_range(range_out, lower_than, include_le,
                                            greater_than, include_ge);
  }

  RecallResultPtr get_topk_result(const std::string& field, int topk,
                                  bool order_asc, offset_filter_t filter) {
    auto itr = field_bitmap_groups_map_.find(field);
    if (itr == field_bitmap_groups_map_.end()) {
      return nullptr;
    }
    return itr->second->get_topk_result(topk, order_asc, filter);
  }

  // topk in center1d
  RecallResultPtr get_topk_result_center1d(const std::string& field, int topk,
                                           bool order_asc, double center1d,
                                           offset_filter_t filter) {
    auto itr = field_bitmap_groups_map_.find(field);
    if (itr == field_bitmap_groups_map_.end()) {
      return nullptr;
    }
    return itr->second->get_topk_result_center1d(topk, order_asc, center1d,
                                                 filter);
  }

  // topk with multi fields
  RecallResultPtr get_topk_result_with_conditions(
      const std::vector<std::string>& fields, int topk,
      std::vector<bool>& order_ascs, offset_filter_t filter) {
    auto itr = field_bitmap_groups_map_.find(fields[0]);
    if (itr == field_bitmap_groups_map_.end()) {
      return nullptr;
    }
    FieldBitmapGroupPtr first_group = itr->second;
    std::vector<std::pair<RangedMapPtr, bool>> conditions;
    for (size_t i = 1; i < fields.size(); i++) {
      itr = field_bitmap_groups_map_.find(fields[i]);
      if (itr == field_bitmap_groups_map_.end()) {
        return nullptr;
      } else if (itr->second->get_rangedmap_ptr() == nullptr) {
        return nullptr;
      }
      conditions.emplace_back(itr->second->get_rangedmap_ptr(), order_ascs[i]);
    }
    return first_group->get_topk_result_with_conditions(topk, order_ascs[0],
                                                        filter, conditions);
  }

  RangedMapPtr get_rangedmap_ptr(const std::string& field) {
    auto itr = field_bitmap_groups_map_.find(field);
    if (itr == field_bitmap_groups_map_.end()) {
      return nullptr;
    }
    return itr->second->get_rangedmap_ptr();
  }

  // RangedMap2D continuous value region search interface
  BitmapPtr make_range2d_copy(const std::vector<std::string>& fields,
                              const std::vector<double>& center,
                              double radius) {
    if (fields.size() != 2UL || fields.size() != center.size()) {
      return nullptr;
    }
    auto itr0 = field_bitmap_groups_map_.find(fields[0]);
    if (itr0 == field_bitmap_groups_map_.end()) {
      return nullptr;
    }
    auto itr1 = field_bitmap_groups_map_.find(fields[1]);
    if (itr1 == field_bitmap_groups_map_.end()) {
      return nullptr;
    }

    auto range_0_ptr = itr0->second->get_rangedmap_ptr();
    auto range_1_ptr = itr1->second->get_rangedmap_ptr();
    if (range_0_ptr && range_1_ptr) {
      RangedMap2D map2d = {*range_0_ptr, *range_1_ptr};
      return map2d.get_range2d_bitmap(center[0], center[1], radius);
    }
    return nullptr;
  }

  // Bitmap inverted index search interface
  BitmapPtr make_full_temp() {
    BitmapPtr temp = std::make_shared<Bitmap>();
    temp->SetRange(0, element_size_);
    return temp;
  }

  int count_field_enums(const std::string& field,
                        std::map<std::string, uint32_t>& enum_count,
                        BitmapPtr valid_bitmap);
  int count_field_enums(const std::vector<std::string>& fields,
                        std::map<std::string, uint32_t>& enum_count,
                        BitmapPtr valid_bitmap);
  virtual BitmapPtr make_field_copy(const std::string& field,
                                    const std::string key);  // for must one
  virtual BitmapPtr make_field_copy(
      const std::string& field,
      const std::vector<std::string> keys);  // for must
  virtual BitmapPtr make_path_field_copy(const std::string& field,
                                         const std::vector<std::string>& keys,
                                         int depth);  // for must
  virtual BitmapPtr make_path_field_exclude_copy(
      const std::string& field, const std::vector<std::string>& keys,
      int depth);
  virtual BitmapPtr make_field_exclude_copy(
      const std::string& field, const std::vector<std::string>& keys);

  // Prefix and contains support
  virtual BitmapPtr make_field_prefix_copy(const std::string& field,
                                           const std::string& prefix);
  virtual BitmapPtr make_field_contains_copy(const std::string& field,
                                             const std::string& substring);
  virtual BitmapPtr make_field_regex_copy(const std::string& field,
                                          const std::string& pattern);

  // Serialization
  int serialize_set_to_stream(std::ofstream& output);
  virtual int parse_set_from_stream(std::ifstream& input);

  bool is_path_field_name(const std::string& field_name) const {
    return path_field_names_.count(field_name) != 0;
  }

  bool convert_label_u64_to_offset(const std::vector<uint64_t>& labels,
                                   std::vector<uint32_t>& offsets) {
    if (!label_u64_offset_converter_) {
      return false;
    }
    return label_u64_offset_converter_(labels, offsets);
  }

  void register_label_offset_converter(
      LABEL_U64_OFFSET_CONVERT_FUNC label_u64_converter) {
    label_u64_offset_converter_ = std::move(label_u64_converter);
  }

 protected:
  size_t element_size_;
  std::string group_set_name_;
  std::set<std::string> field_names_;
  std::set<std::string> range_field_names_;
  std::set<std::string> enum_field_names_;
  std::set<std::string> path_field_names_;
  std::map<std::string, FieldBitmapGroupPtr> field_bitmap_groups_map_;
  LABEL_U64_OFFSET_CONVERT_FUNC label_u64_offset_converter_;
};

}  // namespace vectordb
