// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include <string>
#include <memory>
#include <filesystem>
#include "index/detail/scalar/bitmap_holder/bitmap_field_group.h"

#include "index/detail/meta/scalar_index_meta.h"
#include "index/detail/fields_dict.h"
#include "index/detail/search_context.h"
namespace vectordb {
class ScalarIndex {
 public:
  ScalarIndex(std::shared_ptr<ScalarIndexMeta> meta,
              const std::filesystem::path& dir = "");

  ScalarIndex();

  virtual ~ScalarIndex() = default;

  int load(const std::filesystem::path& dir);

  int add_row_data(int offset, const FieldsDict& fields,
                   const FieldsDict& old_fields);

  int delete_row_data(int offset, const FieldsDict& old_fields);

  FieldBitmapGroupSetPtr get_field_sets() {
    return field_sets_;
  }

  int dump(const std::filesystem::path& dir);

 private:
  FieldBitmapGroupSetPtr field_sets_;
};

}  // namespace vectordb