// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#include "index/detail/scalar/scalar_index.h"
#include "spdlog/spdlog.h"
#include <ostream>
#include "index/detail/scalar/filter/filter_ops.h"

namespace vectordb {

const std::string kIndexDataFile = "scalar_index.data";

int get_type_id(const std::string& field_type) {
  if (field_type == "int64") {
    return BitmapGroupBase::kBitmapGroupBothBitmapsAndRange;
  } else if (field_type == "float32") {
    return BitmapGroupBase::kBitmapGroupRangedMap;
  } else if (field_type == "string" || field_type == "bool") {
    return BitmapGroupBase::kBitmapGroupBitmaps;
  } else if (field_type == "path") {
    return BitmapGroupBase::kBitmapGroupDir;
  } else {
    SPDLOG_ERROR("bitmap_group_unknown {}", field_type);
    return BitmapGroupBase::kBitmapGroupUnknown;
  }
}

ScalarIndex::ScalarIndex(std::shared_ptr<ScalarIndexMeta> meta,
                         const std::filesystem::path& dir)
    : field_sets_(std::make_shared<FieldBitmapGroupSet>("default")) {
  if (!dir.empty()) {
    auto pt = dir / kIndexDataFile;
    std::ifstream input(pt, std::ios::binary);
    if (!input) {
      throw std::runtime_error("ScalarIndex::ScalarIndex open file failed");
    }
    field_sets_->parse_set_from_stream(input);
    input.close();
  } else {
    for (auto& iter : meta->items) {
      auto& field_name = iter.first;
      auto& item = iter.second;
      int type_id = get_type_id(item.field_type);
      if (type_id != BitmapGroupBase::kBitmapGroupUnknown) {
        auto group_ptr =
            std::make_shared<FieldBitmapGroup>("", field_name, type_id);
        field_sets_->add_field_group(group_ptr);
      }
    }
  }
}

int ScalarIndex::load(const std::filesystem::path& dir) {
  auto pt = dir / kIndexDataFile;
  std::ifstream input(pt, std::ios::binary);
  if (!input) {
    return -1;
  }
  field_sets_->parse_set_from_stream(input);

  input.close();
  return 0;
}

int ScalarIndex::add_row_data(int offset, const FieldsDict& fields,
                              const FieldsDict& old_fields) {
  if (!old_fields.empty()) {
    if (!old_fields.str_kv_map_.empty()) {
      field_sets_->delete_field_data(old_fields.str_kv_map_, offset);
    }
    if (!old_fields.dbl_kv_map_.empty()) {
      field_sets_->delete_field_data(old_fields.dbl_kv_map_, offset);
    }
  }

  if (!fields.empty()) {
    if (!fields.str_kv_map_.empty()) {
      field_sets_->add_field_data(fields.str_kv_map_, offset);
    }
    if (!fields.dbl_kv_map_.empty()) {
      field_sets_->add_field_data(fields.dbl_kv_map_, offset);
    }
  }

  return 0;
}

int ScalarIndex::delete_row_data(int offset, const FieldsDict& old_fields) {
  if (!old_fields.empty()) {
    if (!old_fields.str_kv_map_.empty()) {
      field_sets_->delete_field_data(old_fields.str_kv_map_, offset);
    }
    if (!old_fields.dbl_kv_map_.empty()) {
      field_sets_->delete_field_data(old_fields.dbl_kv_map_, offset);
    }
  }
  return 0;
}

int ScalarIndex::dump(const std::filesystem::path& dir) {
  auto pt = dir / kIndexDataFile;

  std::ofstream output(pt, std::ios::binary);
  field_sets_->serialize_set_to_stream(output);
  output.close();
  return 0;
}

}  // namespace vectordb
