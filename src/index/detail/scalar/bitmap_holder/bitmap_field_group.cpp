// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#include "bitmap_field_group.h"
#include "index/detail/scalar/bitmap_holder/dir_index.h"
#include "common/io_utils.h"

#include <sstream>
#include <regex>

namespace vectordb {

// BitmapGroupBase

BitmapGroupBase::BitmapGroupBase(const std::string group_set_name,
                                 const std::string group_name,
                                 const int type_id)
    : group_set_name_(group_set_name),
      group_name_(group_name),
      type_id_(type_id) {
  if (type_id_ == kBitmapGroupBothBitmapsAndRange ||
      type_id_ == kBitmapGroupRangedMap) {
    rangedmap_ptr_ = std::make_shared<RangedMap>();
  }
  if (type_id_ == kBitmapGroupDir) {
    dir_index_ = std::make_shared<DirIndex>();
  }
}

BitmapGroupBase::~BitmapGroupBase() {
  clear();
}

void BitmapGroupBase::clear() {
  bitmap_group_.clear();
  rangedmap_ptr_ = nullptr;
  dir_index_ = nullptr;
  _clear();
}

RangedMap* BitmapGroupBase::get_editable_rangedmap() {
  if (rangedmap_ptr_ == nullptr) {
    rangedmap_ptr_ = std::make_shared<RangedMap>();
  }
  return rangedmap_ptr_.get();
}

bool BitmapGroupBase::exist_bitmap(const std::string& key) {
  return bitmap_group_.find(key) != bitmap_group_.end();
}

const Bitmap* BitmapGroupBase::get_bitmap(const std::string& key) {
  return get_editable_bitmap(key);
}

Bitmap* BitmapGroupBase::get_editable_bitmap(const std::string& key) {
  auto it = bitmap_group_.find(key);
  if (it != bitmap_group_.end()) {
    return it->second.get();
  }

  auto new_map_ptr = std::make_shared<Bitmap>();
  bitmap_group_[key] = new_map_ptr;
  return new_map_ptr.get();
}

BitmapPtr BitmapGroupBase::get_bitmap_copy(const std::string& key) {
  auto temp = get_bitmap(key);
  if (!temp) {
    return nullptr;
  }
  BitmapPtr new_copy = std::make_shared<Bitmap>();
  new_copy->copy(temp);
  return new_copy;
}

BitmapPtr BitmapGroupBase::get_bitmap_in_range(bool range_out,
                                               double lower_than,
                                               bool include_le,
                                               double greater_than,
                                               bool include_ge) {
  if (rangedmap_ptr_ == nullptr) {
    SPDLOG_ERROR(
        "get_bitmap_in_range for L:{} H:{} failed, rangedmap_ptr_ nullptr",
        lower_than, greater_than);
    return nullptr;
  }
  return rangedmap_ptr_->get_range_bitmap(range_out, lower_than, include_le,
                                          greater_than, include_ge);
}

RecallResultPtr BitmapGroupBase::get_topk_result(int topk, bool order_asc,
                                                 offset_filter_t filter) {
  if (rangedmap_ptr_ == nullptr) {
    SPDLOG_ERROR("get_topk_result {} for {} failed, rangedmap_ptr_ is nullptr",
                 topk, group_name_);
    return nullptr;
  }
  return rangedmap_ptr_->get_topk_result(topk, order_asc, filter);
}

RecallResultPtr BitmapGroupBase::get_topk_result_center1d(
    int topk, bool order_asc, double center1d, offset_filter_t filter) {
  if (rangedmap_ptr_ == nullptr) {
    SPDLOG_ERROR("get_topk_result {} for {} failed, rangedmap_ptr_ is nullptr",
                 topk, group_name_);
    return nullptr;
  }
  return rangedmap_ptr_->get_topk_result_center1d(topk, order_asc, center1d,
                                                  filter);
}

RecallResultPtr BitmapGroupBase::get_topk_result_with_conditions(
    int topk, bool this_order_asc, offset_filter_t filter,
    std::vector<std::pair<RangedMapPtr, bool>> conditions) {
  if (rangedmap_ptr_ == nullptr) {
    SPDLOG_ERROR("get_topk_result {} for {} failed, rangedmap_ptr_ is nullptr",
                 topk, group_name_);
    return nullptr;
  }
  return rangedmap_ptr_->get_topk_result_with_conditions(topk, this_order_asc,
                                                         filter, conditions);
}

bool BitmapGroupBase::is_valid() {
  if (bitmap_group_.size() <= 0 && rangedmap_ptr_ == nullptr) {
    return false;
  }
  if (group_name_.empty()) {
    return false;
  }
  return true;
}

int BitmapGroupBase::count_field_enums(
    std::map<std::string, uint32_t>& enum_count,
    std::map<std::string, const BitmapPtr>& enum_bitmaps,
    BitmapPtr valid_bitmap) {
  if (valid_bitmap) {
    // With filter condition, perform intersection calculation
    if (!bitmap_group_.empty()) {
      for (auto& kv : bitmap_group_) {
        if (!kv.second) {
          enum_count[kv.first] = 0;
          continue;
        }
        auto key_bitmap = get_bitmap_copy(kv.first);
        key_bitmap->Intersect(valid_bitmap.get());
        enum_count[kv.first] = key_bitmap->get_cached_nbit();
        enum_bitmaps.insert({kv.first, key_bitmap});
      }
    }
    if (rangedmap_ptr_) {
      enum_count[group_name_] = valid_bitmap->get_cached_nbit();
    }
  } else {
    // Without filter condition, calculate directly
    if (!bitmap_group_.empty()) {
      for (auto& kv : bitmap_group_) {
        if (kv.second) {
          enum_count[kv.first] = kv.second->get_cached_nbit();
          enum_bitmaps.insert({kv.first, kv.second});
        } else {
          enum_count[kv.first] = 0;
        }
      }
    }
    if (rangedmap_ptr_) {
      enum_count[group_name_] = rangedmap_ptr_->size();
    }
  }
  return 0;
}

int BitmapGroupBase::count_field_enums(
    std::map<std::string, uint32_t>& enum_count,
    std::map<std::string, const BitmapPtr>& first_enum_bitmaps) {
  // Combine second field with first field distribution to get joint key distribution
  for (auto& pkv : first_enum_bitmaps) {
    BitmapPtr valid_bitmap = pkv.second;
    if (valid_bitmap) {
      std::string prefix = pkv.first;
      if (!bitmap_group_.empty()) {
        for (auto& kv : bitmap_group_) {
          std::string full_key = prefix + "," + kv.first;
          if (!kv.second) {
            continue;
          }
          auto key_bitmap = get_bitmap_copy(kv.first);
          key_bitmap->Intersect(valid_bitmap.get());
          uint32_t cnt = key_bitmap->get_cached_nbit();
          if (cnt > 0) {
            enum_count[full_key] = cnt;
          }
        }
      }
      if (rangedmap_ptr_) {
        enum_count[prefix] = valid_bitmap->get_cached_nbit();
      }
    }
  }
  return 0;
}

int FieldBitmapGroup::serialize_to_stream(std::ofstream& output) {
  const int bitmap_type_id = get_type_id();
  int bitmap_num = bitmap_group_.size();
  int element_num = (int)element_size_;

  write_bin(output, bitmap_type_id);
  write_bin(output, bitmap_num);
  write_bin(output, element_num);

  if (bitmap_type_id == kBitmapGroupBitmaps) {
    for (auto& itr : bitmap_group_) {
      std::string temp_key = itr.first;
      std::string temp_data;
      itr.second->SerializeToString(temp_data);
      write_str(output, temp_key);
      write_str(output, temp_data);
    }

  } else if (bitmap_type_id == kBitmapGroupRangedMap) {
    if (!rangedmap_ptr_) {
      rangedmap_ptr_ = std::make_shared<RangedMap>();
    }
    rangedmap_ptr_->SerializeToStream(output);
  } else if (bitmap_type_id == kBitmapGroupBothBitmapsAndRange) {
    for (auto& itr : bitmap_group_) {
      std::string temp_key = itr.first;
      std::string temp_data;
      itr.second->SerializeToString(temp_data);
      write_str(output, temp_key);
      write_str(output, temp_data);
    }
    rangedmap_ptr_->SerializeToStream(output);

  } else if (bitmap_type_id == kBitmapGroupDir) {
    for (auto& itr : bitmap_group_) {
      std::string temp_key = itr.first;
      std::string temp_data;
      itr.second->SerializeToString(temp_data);
      write_str(output, temp_key);
      write_str(output, temp_data);
    }

    dir_index_->serialize_to_stream(output);

  } else {
    SPDLOG_ERROR("FieldBitmapGroup unknown bitmap_type_id {}", bitmap_type_id);
    return -1;
  }

  return 0;
}

int FieldBitmapGroup::parse_from_stream(std::ifstream& input) {
  clear();
  // read data
  int bitmap_type_id;
  int bitmap_num = 0;
  int element_num = 0;
  read_bin(input, bitmap_type_id);
  set_type_id(bitmap_type_id);
  if (bitmap_type_id != kBitmapGroupBitmaps &&
      bitmap_type_id != kBitmapGroupRangedMap &&
      bitmap_type_id != kBitmapGroupBothBitmapsAndRange &&
      bitmap_type_id != kBitmapGroupDir) {
    return -1;
  }
  try {
    read_bin(input, bitmap_num);
    read_bin(input, element_num);
    if (bitmap_num < 0) {
      SPDLOG_ERROR(
          "FieldBitmapGroup parse_from_stream bitmap_num invalid {} < 0",
          bitmap_num);
      return -2;
    }
    if (bitmap_type_id == kBitmapGroupBitmaps) {
      for (int i = 0; i < bitmap_num; i++) {
        std::string temp_key;
        std::string temp_data;
        read_str(input, temp_key);
        read_str(input, temp_data);
        bitmap_group_[temp_key] = std::make_shared<Bitmap>();
        bitmap_group_[temp_key]->ParseFromString(temp_data);
      }
    } else if (bitmap_type_id == kBitmapGroupRangedMap) {
      rangedmap_ptr_ = std::make_shared<RangedMap>();
      rangedmap_ptr_->ParseFromStream(input);
    } else if (bitmap_type_id == kBitmapGroupBothBitmapsAndRange) {
      // Ensure read order matches write order
      // bitmaps
      for (int i = 0; i < bitmap_num; i++) {
        std::string temp_key;
        std::string temp_data;
        read_str(input, temp_key);
        read_str(input, temp_data);
        bitmap_group_[temp_key] = std::make_shared<Bitmap>();
        bitmap_group_[temp_key]->ParseFromString(temp_data);
      }
      // range_map
      rangedmap_ptr_ = std::make_shared<RangedMap>();
      rangedmap_ptr_->ParseFromStream(input);

    } else if (bitmap_type_id == kBitmapGroupDir) {
      for (int i = 0; i < bitmap_num; i++) {
        std::string temp_key;
        std::string temp_data;
        read_str(input, temp_key);
        read_str(input, temp_data);
        bitmap_group_[temp_key] = std::make_shared<Bitmap>();
        bitmap_group_[temp_key]->ParseFromString(temp_data);  // portable=true
      }

      dir_index_ = std::make_shared<DirIndex>();
      dir_index_->parse_from_stream(input);
    }
  } catch (std::exception& e) {
    // SPDLOG_ERROR("FieldBitmapGroup parse_from_stream exception {}",
    // e.what());
    return -3;
  }
  element_size_ = element_num;
  return 0;
}

// FieldBitmapGroupSet

FieldBitmapGroupPtr FieldBitmapGroupSet::find_field_group(
    const std::string& field_name) {
  auto itr = field_bitmap_groups_map_.find(field_name);
  if (itr != field_bitmap_groups_map_.end()) {
    return itr->second;
  }
  return nullptr;
}

int FieldBitmapGroupSet::add_field_group(
    FieldBitmapGroupPtr field_bitmap_group) {
  if (!field_bitmap_group) {
    SPDLOG_ERROR(
        "FieldBitmapGroupSet::add_field_group invalid field_bitmap_group");
    return -1;
  }
  const std::string& field_name = field_bitmap_group->group_name();
  auto itr = field_bitmap_groups_map_.find(field_name);
  if (itr != field_bitmap_groups_map_.end()) {
    SPDLOG_ERROR("FieldBitmapGroupSet::add_field_group duplicated field {}",
                 field_name);
    return -2;
  }
  field_bitmap_groups_map_[field_name] = field_bitmap_group;
  field_names_.insert(field_name);
  if (field_bitmap_group->get_type_id() ==
          BitmapGroupBase::kBitmapGroupRangedMap ||
      field_bitmap_group->get_type_id() ==
          BitmapGroupBase::kBitmapGroupBothBitmapsAndRange) {
    range_field_names_.insert(field_name);
  }
  if (field_bitmap_group->get_type_id() ==
          BitmapGroupBase::kBitmapGroupBitmaps ||
      field_bitmap_group->get_type_id() ==
          BitmapGroupBase::kBitmapGroupBothBitmapsAndRange) {
    enum_field_names_.insert(field_name);
  }
  if (field_bitmap_group->get_type_id() == BitmapGroupBase::kBitmapGroupDir) {
    path_field_names_.insert(field_name);
  }
  // element_size_ = field_bitmap_group->element_size();
  return 0;
}

int FieldBitmapGroupSet::count_field_enums(
    const std::string& field, std::map<std::string, uint32_t>& enum_count,
    BitmapPtr valid_bitmap) {
  if (field_bitmap_groups_map_.find(field) == field_bitmap_groups_map_.end()) {
    return -1;
  }
  std::map<std::string, const BitmapPtr> enum_bitmaps;
  return field_bitmap_groups_map_[field]->count_field_enums(
      enum_count, enum_bitmaps, valid_bitmap);
}

int FieldBitmapGroupSet::count_field_enums(
    const std::vector<std::string>& fields,
    std::map<std::string, uint32_t>& enum_count, BitmapPtr valid_bitmap) {
  // Support two fields
  if (fields.size() == 1UL) {
    return count_field_enums(fields[0], enum_count, valid_bitmap);
  } else if (fields.size() > 2UL || fields.size() < 1UL) {
    return -2;
  }
  if (field_bitmap_groups_map_.find(fields[0]) ==
      field_bitmap_groups_map_.end()) {
    return -3;
  }
  if (field_bitmap_groups_map_.find(fields[1]) ==
      field_bitmap_groups_map_.end()) {
    return -4;
  }
  std::map<std::string, const BitmapPtr> enum_bitmaps;
  std::map<std::string, uint32_t> temp_enum_count;
  int ret = field_bitmap_groups_map_[fields[0]]->count_field_enums(
      temp_enum_count, enum_bitmaps, valid_bitmap);
  if (ret != 0) {
    return ret;
  }
  return field_bitmap_groups_map_[fields[1]]->count_field_enums(enum_count,
                                                                enum_bitmaps);
}

BitmapPtr FieldBitmapGroupSet::make_field_copy(
    const std::string& field, const std::vector<std::string> keys) {
  // Actual calculation: merge
  if (keys.size() == 1) {
    return make_field_copy(field, keys[0]);
  } else if (keys.size() > 1) {
    BitmapPtr temp = std::make_shared<Bitmap>();
    std::vector<const Bitmap*> to_unions;
    for (size_t i = 0; i < keys.size(); i++) {
      const Bitmap* temp_i = get_bitmap(field, keys[i]);
      if (!temp_i) {
        // Under OR semantics, allow missing fields to be ignored
        continue;
      }
      to_unions.emplace_back(temp_i);
    }
    if (to_unions.size() > 0) {
      temp->FastUnion(to_unions);
    }
    return temp;
  }
  return nullptr;
}

BitmapPtr FieldBitmapGroupSet::make_field_copy(const std::string& field,
                                               const std::string key) {
  auto itr = field_bitmap_groups_map_.find(field);
  if (itr == field_bitmap_groups_map_.end()) {
    return nullptr;
  }
  return itr->second->get_bitmap_copy(key);
}

BitmapPtr FieldBitmapGroupSet::make_path_field_copy(
    const std::string& field, const std::vector<std::string>& keys, int depth) {
  auto itr = field_bitmap_groups_map_.find(field);
  if (itr == field_bitmap_groups_map_.end()) {
    return nullptr;
  }
  if (depth == -1) {
    for (const auto& path_prefix : keys) {
      if (path_prefix == "/" || path_prefix == "") {
        return make_full_temp();
      }
    }
  }

  auto group = itr->second;
  auto dip = group->get_dir_index();
  if (!dip) {
    return nullptr;
  }

  std::vector<const Bitmap*> bitmaps_to_union;
  std::unordered_set<std::string> all_unique_bitmaps;

  for (const auto& path_prefix : keys) {
    std::unordered_set<std::string> unique_bitmaps;
    dip->get_merged_bitmap(path_prefix, depth, unique_bitmaps);
    all_unique_bitmaps.insert(unique_bitmaps.begin(), unique_bitmaps.end());
  }

  bitmaps_to_union.reserve(all_unique_bitmaps.size());
  for (const auto& bitmap_key : all_unique_bitmaps) {
    const Bitmap* bm = group->get_bitmap(bitmap_key);
    if (bm) {
      bitmaps_to_union.push_back(bm);
    }
  }

  auto final_bitmap = std::make_shared<Bitmap>();
  if (!bitmaps_to_union.empty()) {
    final_bitmap->FastUnion(bitmaps_to_union);
  }
  return final_bitmap;
}

BitmapPtr FieldBitmapGroupSet::make_path_field_exclude_copy(
    const std::string& field, const std::vector<std::string>& keys, int depth) {
  BitmapPtr pres = make_full_temp();
  pres->Exclude(make_path_field_copy(field, keys, depth).get());
  return pres;
}

BitmapPtr FieldBitmapGroupSet::make_field_exclude_copy(
    const std::string& field, const std::vector<std::string>& keys) {
  // Actual calculation: exclusion method
  BitmapPtr pres = make_full_temp();
  if (keys.size() <= 0) {
    return pres;
  } else if (keys.size() == 1) {
    const Bitmap* temp_p = get_bitmap(field, keys[0]);
    if (temp_p) {
      pres->Exclude(temp_p);
    }
  } else {
    BitmapPtr temp = make_field_copy(field, keys);
    if (temp) {
      pres->Exclude(temp.get());
    }
  }

  return pres;
}

BitmapPtr FieldBitmapGroupSet::make_field_prefix_copy(
    const std::string& field, const std::string& prefix) {
  auto itr = field_bitmap_groups_map_.find(field);
  if (itr == field_bitmap_groups_map_.end()) {
    return nullptr;
  }

  std::string search_prefix = prefix;
  if (is_path_field_name(field)) {
    if (!search_prefix.empty() && search_prefix[0] != '/') {
      search_prefix = "/" + search_prefix;
    }
  }

  return itr->second->get_bitmap_by_prefix(search_prefix);
}

BitmapPtr FieldBitmapGroupSet::make_field_contains_copy(
    const std::string& field, const std::string& substring) {
  auto itr = field_bitmap_groups_map_.find(field);
  if (itr == field_bitmap_groups_map_.end()) {
    return nullptr;
  }
  return itr->second->get_bitmap_by_contains(substring);
}

BitmapPtr FieldBitmapGroupSet::make_field_regex_copy(
    const std::string& field, const std::string& pattern) {
  auto itr = field_bitmap_groups_map_.find(field);
  if (itr == field_bitmap_groups_map_.end()) {
    return nullptr;
  }
  return itr->second->get_bitmap_by_regex(pattern);
}

const int kGroupSetVersion1 = 1;

int FieldBitmapGroupSet::serialize_set_to_stream(std::ofstream& output) {
  int save_version = kGroupSetVersion1;
  int bitmap_num = field_bitmap_groups_map_.size();
  int element_num = (int)element_size_;
  write_bin(output, save_version);
  write_bin(output, bitmap_num);
  write_bin(output, element_num);
  std::vector<std::string> field_names;
  if (bitmap_num > 0 || element_num > 0) {
    int ret = 0;
    for (auto& iter : field_bitmap_groups_map_) {
      std::string field_name = iter.first;
      field_names.emplace_back(field_name);
      if (!iter.second || field_name.empty()) {
        SPDLOG_ERROR(
            "FieldBitmapGroupSet::serialize_set_to_stream wrong data: [{}]",
            field_name);
        return -1;
      }
      write_str(output, field_name);
      ret = iter.second->serialize_to_stream(output);
      if (ret != 0) {
        SPDLOG_ERROR("FieldBitmapGroupSet::serialize_to_stream failed: {}",
                     ret);
        return ret;
      }
    }
  }
  return 0;
}

int FieldBitmapGroupSet::parse_set_from_stream(std::ifstream& input) {
  int save_version;
  int bitmap_num = 0;
  int element_num = 0;
  read_bin(input, save_version);
  if (save_version != kGroupSetVersion1) {
    SPDLOG_ERROR("FieldBitmapGroupSet group_set_version_1 {}", save_version);
    return -1;
  }
  read_bin(input, bitmap_num);
  read_bin(input, element_num);
  if (bitmap_num > 0 || element_num > 0) {
    int ret = 0;
    for (int i = 0; i < bitmap_num; i++) {
      std::string field_name;
      read_str(input, field_name);

      FieldBitmapGroupPtr group_ptr_i =
          std::make_shared<FieldBitmapGroup>(group_set_name_, field_name);
      ret = group_ptr_i->parse_from_stream(input);
      if (ret != 0) {
        SPDLOG_ERROR("FieldBitmapGroupSet field_name {} parse failed {}",
                     field_name, ret);
        return ret;
      }
      ret = add_field_group(group_ptr_i);
      if (ret != 0) {
        SPDLOG_ERROR(
            "FieldBitmapGroupSet field_name {} add_field_group failed {}",
            field_name, ret);
        return ret;
      }
    }
  }
  element_size_ = element_num;
  return 0;
}

// BitmapGroupBase prefix and contains support

BitmapPtr BitmapGroupBase::get_bitmap_by_prefix(const std::string& prefix) {
  if (bitmap_group_.empty()) {
    return nullptr;
  }

  BitmapPtr result = nullptr;
  // SPDLOG_INFO("get_bitmap_by_prefix: {}", prefix);
  //  Iterate through all keys and union bitmaps whose keys start with prefix
  for (const auto& kv : bitmap_group_) {
    const std::string& key = kv.first;
    if (key.size() >= prefix.size() &&
        key.compare(0, prefix.size(), prefix) == 0) {
      // Key matches prefix
      if (!kv.second) {
        continue;
      }

      if (result == nullptr) {
        // First match - create copy
        result = std::make_shared<Bitmap>();
        result->copy(kv.second.get());
      } else {
        // Union with existing result
        result->Union(kv.second.get());
      }
    }
  }

  return result;
}

BitmapPtr BitmapGroupBase::get_bitmap_by_contains(
    const std::string& substring) {
  if (bitmap_group_.empty()) {
    return nullptr;
  }

  BitmapPtr result = nullptr;

  // Iterate through all keys and union bitmaps whose keys contain substring
  for (const auto& kv : bitmap_group_) {
    const std::string& key = kv.first;
    if (key.find(substring) != std::string::npos) {
      // Key contains substring
      if (!kv.second) {
        continue;
      }

      if (result == nullptr) {
        // First match - create copy
        result = std::make_shared<Bitmap>();
        result->copy(kv.second.get());
      } else {
        // Union with existing result
        result->Union(kv.second.get());
      }
    }
  }

  return result;
}

BitmapPtr BitmapGroupBase::get_bitmap_by_regex(const std::string& pattern) {
  if (bitmap_group_.empty()) {
    return nullptr;
  }

  BitmapPtr result = nullptr;

  try {
    // Compile regex pattern
    std::regex regex_pattern(pattern);

    // Iterate through all keys and union bitmaps whose keys match the regex
    for (const auto& kv : bitmap_group_) {
      const std::string& key = kv.first;
      if (std::regex_search(key, regex_pattern)) {
        // Key matches regex
        if (!kv.second) {
          continue;
        }

        if (result == nullptr) {
          // First match - create copy
          result = std::make_shared<Bitmap>();
          result->copy(kv.second.get());
        } else {
          // Union with existing result
          result->Union(kv.second.get());
        }
      }
    }
  } catch (const std::regex_error& e) {
    SPDLOG_ERROR(
        "get_bitmap_by_regex failed with invalid regex pattern '{}': {}",
        pattern, e.what());
    return nullptr;
  }

  return result;
}

}  // namespace vectordb
