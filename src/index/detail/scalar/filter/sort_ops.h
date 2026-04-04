// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <random>

#include "op_base.h"

namespace vectordb {

/* Recall query condition clauses
   sort, random_sort, count
{
  // for one field and one order
  "op": "sort",
  "field": "score", // required, str
  "order": "desc",  // optional, str["desc", "asc"], default("desc")
  "topk": 100
}
{
  // for multi fields and multi orders
  "op": "sort",
  "field": ["score1", "score2"],  // required, list[str]
  "order": ["desc", "asc"],       // optional, list[str["desc", "asc"]],
default(["desc"...]) "topk": 100
}
{
  // for center1d
  "op": "sort",
  "type": "center1d", // required, str["center1d"]
  "field": "score",   // required, str
  "order": "desc",    // optional, str["desc", "asc"], default("asc"). only
support asc now "center": 200.0     // required, double "topk": 100
}
{
  // for geocenter2d
  "op": "sort",
  "type": "geocenter2d",              // required, str["geocenter2d"]
  "field": ["longi", "lati"],         // required, list[str], len = 2
  "order": "desc",                    // optional, str["desc", "asc"],
default("asc"). "center": [123.4567, 12.3456],      // required, list[double],
len = 2, longitude/latitude center point, (-180~180, -90~90) "topk": 100
}
*/
SorterOpBasePtr make_sorter_op_by_opname(const std::string& opname);
SorterOpBasePtr parse_sorter_json_doc(const JsonDoc& json_doc);
SorterOpBasePtr parse_sorter_json_str(const std::string& json_str);
SorterOpBasePtr parse_sorter_json_doc_outter(const JsonDoc& json_doc);

class SorterOp : public SorterOpBase {
 public:
  SorterOp()
      : type_(SorterOpType::SORT_SINGLE_1D),
        order_ascs_({false}),
        center1d_(0),
        topk_(0) {
  }  // Default descending order
  virtual ~SorterOp() {
  }
  virtual std::string op_name() const override {
    return "sort";
  }
  // api
  virtual void set_field(const std::string& field) {
    fields_.clear();
    fields_.push_back(field);
  }
  virtual void set_field(std::string&& field) {
    fields_.clear();
    fields_.emplace_back(std::move(field));
  }
  virtual void set_fields(const std::vector<std::string>& fields) {
    fields_ = fields;
  }
  virtual void set_fields(std::vector<std::string>&& fields) {
    fields_ = std::move(fields);
  }
  virtual void set_order_asc(bool order_asc) {
    type_ = SorterOpType::SORT_SINGLE_1D;
    order_ascs_.clear();
    order_ascs_.push_back(order_asc);
    if (fields_.size() != 1U) {
      SPDLOG_WARN(
          "SorterOp::set_order_asc invalid: assert fields_.size() == 1, but now it is %zu",
          fields_.size());
    } else {
      valid_ = true;
    }
  }
  virtual void set_order_ascs(const std::vector<bool>& order_ascs) {
    order_ascs_ = order_ascs;
    validate_order_ascs();
  }
  virtual void set_order_ascs(std::vector<bool>&& order_ascs) {
    order_ascs_ = std::move(order_ascs);
    validate_order_ascs();
  }
  virtual void set_center1d(bool order_asc, double center1d) {
    type_ = SorterOpType::SORT_CENTER_1D;
    order_ascs_.clear();
    order_ascs_.push_back(order_asc);
    center1d_ = center1d;
    if (fields_.size() != 1U) {
      SPDLOG_WARN(
          "SorterOp::set_center1d invalid: assert fields_.size() == 1, but now it is %zu",
          fields_.size());
    } else if (order_asc != true) {
      SPDLOG_WARN(
          "SorterOp::set_center1d invalid: no implementation for order_asc == true");
    } else {
      valid_ = true;
    }
  }

  virtual void set_topk(int topk) {
    topk_ = topk;
  }
  virtual int get_topk() const {
    return topk_;
  }

  virtual bool is_leaf_op() const override {
    return true;
  }
  virtual JsonDocPtr get_json_doc();
  virtual int load_json_doc(const JsonValue& json_doc);

  virtual RecallResultPtr calc_topk_result(
      FieldBitmapGroupSetPtr field_group_set_ptr, BitmapPtr valid_bitmap);
  virtual RecallResultPtr calc_topk_result(
      FieldBitmapGroupSetPtr field_group_set_ptr, offset_filter_t filter);
  virtual RecallResultPtr calc_topk_result(
      FieldBitmapGroupSetPtr field_group_set_ptr, BitmapPtr valid_bitmap,
      offset_filter_t filter);

 protected:
  static int load_json_doc_load_fields(const JsonValue& json_doc,
                                       std::vector<std::string>& fields);
  static int load_json_doc_load_order_ascs(const JsonValue& json_doc,
                                           std::vector<bool>& order_ascs);
  static int load_json_doc_load_centers(const JsonValue& json_doc,
                                        std::vector<double>& centers);
  static int load_json_doc_load_topk(const JsonValue& json_doc, int& topk);
  static int load_json_doc_load_type(const JsonValue& json_doc,
                                     SorterOpType& type);
  int load_json_doc_validate(const JsonValue& json_doc,
                             std::vector<double>& centers);

  bool _is_small_ratio_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                              uint32_t valid_size);
  virtual RecallResultPtr _calc_topk_result_with_small_bitmap(
      FieldBitmapGroupSetPtr field_group_set_ptr, BitmapPtr valid_bitmap,
      offset_filter_t filter = nullptr);

  void validate_order_ascs() {
    type_ = SorterOpType::SORT_MULTI_SINGLE_1D;
    if (fields_.size() == 0) {
      SPDLOG_WARN(
          "SorterOp::set_order_ascs invalid: assert fields_.size() != 0");
    } else if (fields_.size() != order_ascs_.size()) {
      SPDLOG_WARN(
          "SorterOp::set_order_ascs invalid: assert fields_.size() == order_ascs_.size(), but now they are %zu and %zu",
          fields_.size(), order_ascs_.size());
    } else {
      valid_ = true;
      if (fields_.size() == 1U) {
        type_ = SorterOpType::SORT_SINGLE_1D;
      }
    }
  }

  SorterOpType type_;
  std::vector<std::string> fields_;
  std::vector<bool> order_ascs_;
  double center1d_;
  int topk_;
};

class CounterOp : public SorterOpBase {
 public:
  CounterOp() {
    valid_ = true;
  }

  virtual ~CounterOp() {
  }

  virtual std::string op_name() const override {
    return "count";
  }

  virtual void set_field(const std::string& field, int greater_than = -1) {
    fields_.clear();
    fields_.push_back(field);
    gt_ = greater_than;
  }
  virtual void set_field(const std::vector<std::string>& fields,
                         int greater_than = -1) {
    fields_.clear();
    fields_ = fields;
    gt_ = greater_than;
  }
  virtual void set_topk(int topk) {
  }
  virtual int get_topk() const {
    return 0;
  }

  virtual bool is_leaf_op() const override {
    return true;
  }
  virtual JsonDocPtr get_json_doc();
  virtual int load_json_doc(const JsonValue& json_doc);

  virtual RecallResultPtr calc_topk_result(
      FieldBitmapGroupSetPtr field_group_set_ptr, BitmapPtr valid_bitmap) {
    return _calc_topk_result(field_group_set_ptr, valid_bitmap);
  }
  virtual RecallResultPtr calc_topk_result(
      FieldBitmapGroupSetPtr field_group_set_ptr,
      offset_filter_t unused_filter) {
    return _calc_topk_result(field_group_set_ptr, nullptr);
  }

  virtual RecallResultPtr calc_topk_result(
      FieldBitmapGroupSetPtr field_group_set_ptr, BitmapPtr valid_bitmap,
      offset_filter_t filter) {
    return _calc_topk_result(field_group_set_ptr, valid_bitmap);
  }

 protected:
  virtual RecallResultPtr _calc_topk_result(
      FieldBitmapGroupSetPtr field_group_set_ptr, BitmapPtr valid_bitmap);

  std::vector<std::string> fields_;
  int gt_ = -1;
  int max_entry_ = 10000;
};

using SorterOpPtr = std::shared_ptr<SorterOp>;

}  // namespace vectordb
