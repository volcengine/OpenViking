// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include <limits.h>
#include <memory>
#include <set>
#include <sstream>
#include <string>
#include <vector>
#include <float.h>
#include "common/json_utils.h"
#include "index/detail/scalar/bitmap_holder/bitmap_field_group.h"

namespace vectordb {

int get_json_float_value(const JsonValue& temp_val, float& value);
int get_json_int_value(const JsonValue& temp_val, int64_t& value);
int parse_and_precheck_op_parts(JsonDoc& json_doc, bool& has_filter,
                                bool& has_sorter);

class OpBase {
 public:
  OpBase() : valid_(false) {
  }
  OpBase(const OpBase& other) = default;
  OpBase(OpBase&& other) noexcept : valid_(other.valid_) {
    other.valid_ = false;
  }
  OpBase& operator=(const OpBase& other) = default;
  OpBase& operator=(OpBase&& other) {
    valid_ = other.valid_;
    other.valid_ = false;
    return *this;
  }
  virtual ~OpBase() {
  }
  virtual std::string op_name() const = 0;
  virtual bool is_valid() const {
    return valid_;
  };
  virtual bool is_empty_conds() {
    return empty_conds_;
  }
  virtual bool is_leaf_op() const = 0;
  void set_valid(const bool valid) {
    valid_ = valid;
  };

  virtual JsonDocPtr get_json_doc() = 0;
  virtual int load_json_doc(const JsonValue& json_doc) = 0;
  virtual std::string dump_str() {
    JsonDocPtr temp = get_json_doc();
    if (!temp) {
      return "";
    }
    return json_stringify(*temp);
  }

 protected:
  bool valid_;
  bool empty_conds_ = false;
};

class FilterOpBase : public OpBase {
  // DSL Filter operator, supports nesting
 public:
  FilterOpBase() = default;
  virtual ~FilterOpBase() = default;
  virtual BitmapPtr calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                                BitmapPtr pres,
                                const std::string on_res_op = "or") = 0;

  virtual bool need_materialized_index() const {
    return true;
  }
};

class SorterOpBase : public OpBase {
 public:
  static const std::string kOrderDescStr;
  static const std::string kOrderAscStr;
  static const std::string kTypeCenter1d;
  enum SorterOpType {
    SORT_SINGLE_1D = 0,
    SORT_MULTI_SINGLE_1D,
    SORT_CENTER_1D,
  };

  SorterOpBase() : OpBase() {
  }
  virtual ~SorterOpBase() {
  }
  virtual int get_topk() const = 0;
  virtual void set_topk(int topk) = 0;

  virtual RecallResultPtr calc_topk_result(
      FieldBitmapGroupSetPtr field_group_set_ptr, BitmapPtr valid_bitmap) = 0;
  virtual RecallResultPtr calc_topk_result(
      FieldBitmapGroupSetPtr field_group_set_ptr, offset_filter_t filter) = 0;
  virtual RecallResultPtr calc_topk_result(
      FieldBitmapGroupSetPtr field_group_set_ptr, BitmapPtr valid_bitmap,
      offset_filter_t filter) = 0;
};

using OpBasePtr = std::shared_ptr<OpBase>;
using FilterOpBasePtr = std::shared_ptr<FilterOpBase>;
using SorterOpBasePtr = std::shared_ptr<SorterOpBase>;

/* DSL advanced options clause
"option": {
  "filter_pre_ann_limit": 200000,
  "filter_pre_ann_ratio": 0.02,
  "rerank_k": 800
}
*/

}  // namespace vectordb