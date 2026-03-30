// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <algorithm>

#include "op_base.h"

namespace vectordb {

/* Filter query condition clauses
{
  "tag": ["Sport", "Game"],  // tag targeting
  "tag_quota": [0, 1000],    // quota for each tag
  "op": "and",
  "conds": [
    {
      "op": "or",
      "conds": [
        {
          "op": "must",
          "field": "music_id",
          "conds": [1,2,3,5,6]
        },
        {
          "op": "must_not",
          "field": "color",
          "conds": ["red"]
        }
      ]
    },
    ...
    {
        "op": "range",
        "field": "price",
        "gte": 1.414,
        "lt": 3.142
    }
    ...
    {
        "op": "range",
        "field": ["pos_x", "pos_y"],
        "center": [32.2, 23.4],
        "radius": 10.0
    }
  ]
}
*/

class LogicOpBase;
class AndOp;
class OrOp;
class FieldOpBase;
class MustOp;
class MustNotOp;
class RangeOp;
using FieldOpBasePtr = std::shared_ptr<FieldOpBase>;
using LogicOpBasePtr = std::shared_ptr<LogicOpBase>;

//

FilterOpBasePtr make_filter_op_by_opname(const std::string& opname);
FilterOpBasePtr parse_filter_json_doc(const JsonDoc& json_doc);
FilterOpBasePtr parse_filter_json_doc_outter(const JsonDoc& json_doc);
FilterOpBasePtr parse_filter_json_str(const std::string& json_str);
FieldOpBasePtr make_field_op_by_opname(const std::string& opname);
LogicOpBasePtr make_logic_op_by_opname(const std::string& opname);
//

class LogicOpBase : public FilterOpBase {
 public:
  virtual ~LogicOpBase() {
  }
  virtual bool is_leaf_op() const override {
    return false;
  }

  virtual BitmapPtr calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                                BitmapPtr pres,
                                const std::string on_res_op = "or");

  virtual void add_cond(FilterOpBasePtr op_ptr) {
    if (op_ptr) {
      logic_conds_.push_back(op_ptr);
    }
  }
  virtual bool is_noop() {
    return false;
  }
  virtual JsonDocPtr get_json_doc();
  void set_ignore_empty_condition(bool ignore_empty_condition) {
    if (op_name() != "and") {
      return;
    }
    ignore_empty_condition_ = ignore_empty_condition;
  }

  bool need_materialized_index() const override {
    return std::any_of(logic_conds_.begin(), logic_conds_.end(),
                       [](std::shared_ptr<vectordb::FilterOpBase> op) {
                         return op->need_materialized_index();
                       });
  }

 protected:
  virtual int parse_conds_ops(const JsonValue& json_doc);

  virtual BitmapPtr calc_self_bitmap(
      FieldBitmapGroupSetPtr field_group_set_ptr);

 protected:
  std::vector<FilterOpBasePtr> logic_conds_;

 private:
  bool ignore_empty_condition_ = false;  // Only effective in AndOp
};

// Logical AND
class AndOp : public LogicOpBase {
 public:
  virtual ~AndOp() {
  }
  virtual std::string op_name() const override {
    return "and";
  }
  virtual int load_json_doc(const JsonValue& json_doc);
};

// Logical OR
class OrOp : public LogicOpBase {
 public:
  virtual ~OrOp() {
  }
  virtual std::string op_name() const override {
    return "or";
  }
  virtual int load_json_doc(const JsonValue& json_doc);
};

// Used to build DSL Builder only with tags recall.
class Noop : public LogicOpBase {
 public:
  virtual ~Noop() {
  }
  virtual std::string op_name() const override {
    return "noop";
  }

  virtual bool is_noop() {
    return true;
  }

  virtual bool is_leaf_op() const override {
    return true;
  }

  virtual int load_json_doc(const JsonValue& json_doc) {
    return 0;
  }

  virtual BitmapPtr calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                                BitmapPtr pres,
                                const std::string on_res_op = "or") {
    return nullptr;
  }

  virtual void add_cond(FilterOpBasePtr op_ptr) {
    return;
  }

  virtual JsonDocPtr get_json_doc() {
    return nullptr;
  }

 protected:
  virtual int parse_conds_ops(const JsonValue& json_doc) {
    return -1;
  }

  BitmapPtr calc_self_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr) {
    return nullptr;
  }
};

class FieldOpBase : public FilterOpBase {
 public:
  virtual ~FieldOpBase() {
  }
  virtual bool is_leaf_op() const override {
    return true;
  }
  virtual int parse_conds_ops(const JsonValue& json_doc);
  virtual JsonDocPtr get_json_doc();
  virtual void set_field(const std::string& field) {
    fields_.clear();
    fields_.push_back(field);
  }
  virtual void set_fields(const std::vector<std::string>& fields) {
    fields_ = fields;
  }
  virtual void set_conds(const std::vector<int64_t>& id_conds) {
    id_conds_ = id_conds;
  }
  virtual void set_conds(const std::vector<std::string>& type_conds) {
    type_conds_ = type_conds;
  }

 protected:
  std::vector<std::string> fields_;
  std::vector<int64_t> id_conds_;
  std::vector<std::string> type_conds_;
};

// Range filter condition
class RangeOp : public FieldOpBase {
 public:
  RangeOp(bool range_out = false) : range_out_(range_out) {
  }
  virtual ~RangeOp() {
  }
  virtual std::string op_name() const override {
    if (range_out_) {
      return "range_out";
    }
    return "range";
  }
  virtual int load_json_doc(const JsonValue& json_doc) override;
  virtual JsonDocPtr get_json_doc();

  virtual BitmapPtr calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                                BitmapPtr pres,
                                const std::string on_res_op = "or");

  virtual void set_greater_than(const double greater_than,
                                const bool is_equal) {
    greater_than_ = greater_than;
    greater_than_equal_ = is_equal;
    set_valid(true);
  }

  virtual void set_less_than(const double less_than, const bool is_equal) {
    less_than_ = less_than;
    less_than_equal_ = is_equal;
    set_valid(true);
  }

  virtual int set_center_radius(const std::vector<double> center,
                                double radius) {
    if (center.size() != 2UL) {
      return -1;
    }
    if (fields_.size() != center.size()) {
      return -2;
    }
    if (radius <= 0.0) {
      return -3;
    }

    center_ = center;
    radius_ = radius;
    set_valid(true);
    return 0;
  }

  virtual int set_center_radius(const std::vector<float> center, float radius) {
    if (center.size() != 2UL) {
      return -1;
    }
    if (fields_.size() != center.size()) {
      return -2;
    }
    if (radius <= 0.0) {
      return -3;
    }

    center_.clear();
    for (auto& f : center) {
      center_.emplace_back(f);
    }
    radius_ = radius;
    set_valid(true);
    return 0;
  }

 protected:
  virtual BitmapPtr calc_self_bitmap(
      FieldBitmapGroupSetPtr field_group_set_ptr);

  // for 1d
  double greater_than_ = -FLT_MAX;
  double less_than_ = FLT_MAX;
  bool greater_than_equal_ = false;
  bool less_than_equal_ = false;
  bool range_out_ = false;
  // for radius condition
  double radius_ = 0.0;
  std::vector<double> center_;
};

// Must match condition
class MustOp : public FieldOpBase {
 public:
  virtual ~MustOp() {
  }
  virtual std::string op_name() const override {
    return "must";
  }
  virtual int load_json_doc(const JsonValue& json_doc);

  virtual BitmapPtr calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                                BitmapPtr pres,
                                const std::string on_res_op = "or");

 protected:
  virtual BitmapPtr calc_self_bitmap(
      FieldBitmapGroupSetPtr field_group_set_ptr);

 private:
  int depth_ = -1;
};

// Must not match condition
class MustNotOp : public FieldOpBase {
 public:
  virtual ~MustNotOp() {
  }
  virtual std::string op_name() const override {
    return "must_not";
  }
  virtual int load_json_doc(const JsonValue& json_doc);

  virtual BitmapPtr calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                                BitmapPtr pres,
                                const std::string on_res_op = "or");

 protected:
  virtual BitmapPtr calc_self_bitmap(
      FieldBitmapGroupSetPtr field_group_set_ptr);

 private:
  int depth_ = -1;
};

// Prefix match condition
class PrefixOp : public FieldOpBase {
 public:
  virtual ~PrefixOp() {
  }
  virtual std::string op_name() const override {
    return "prefix";
  }
  virtual int load_json_doc(const JsonValue& json_doc);

  virtual BitmapPtr calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                                BitmapPtr pres,
                                const std::string on_res_op = "or");

  virtual void set_prefix(const std::string& prefix) {
    prefix_value_ = prefix;
    set_valid(true);
  }

 protected:
  virtual BitmapPtr calc_self_bitmap(
      FieldBitmapGroupSetPtr field_group_set_ptr);

 private:
  std::string prefix_value_;
};

// Contains match condition
class ContainsOp : public FieldOpBase {
 public:
  virtual ~ContainsOp() {
  }
  virtual std::string op_name() const override {
    return "contains";
  }
  virtual int load_json_doc(const JsonValue& json_doc);

  virtual BitmapPtr calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                                BitmapPtr pres,
                                const std::string on_res_op = "or");

  virtual void set_substring(const std::string& substring) {
    substring_value_ = substring;
    set_valid(true);
  }

 protected:
  virtual BitmapPtr calc_self_bitmap(
      FieldBitmapGroupSetPtr field_group_set_ptr);

 private:
  std::string substring_value_;
};

// Regex match condition
class RegexOp : public FieldOpBase {
 public:
  virtual ~RegexOp() {
  }
  virtual std::string op_name() const override {
    return "regex";
  }
  virtual int load_json_doc(const JsonValue& json_doc);

  virtual BitmapPtr calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                                BitmapPtr pres,
                                const std::string on_res_op = "or");

  virtual void set_pattern(const std::string& pattern) {
    pattern_value_ = pattern;
    set_valid(true);
  }

 protected:
  virtual BitmapPtr calc_self_bitmap(
      FieldBitmapGroupSetPtr field_group_set_ptr);

 private:
  std::string pattern_value_;
};

// Must match condition
class LabelInOp : public FilterOpBase {
 public:
  virtual ~LabelInOp() {
  }

  virtual std::string op_name() const override {
    return "label_in";
  }
  virtual bool is_leaf_op() const override {
    return true;
  }
  virtual JsonDocPtr get_json_doc();
  virtual int load_json_doc(const JsonValue& json_doc);

  virtual BitmapPtr calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                                BitmapPtr pres,
                                const std::string on_res_op = "or");

  void set_labels(const std::vector<uint64_t> labels) {
    label_u64_ = labels;
    set_valid(true);
  }

  bool need_materialized_index() const override {
    return false;
  }

 private:
  virtual BitmapPtr calc_self_bitmap(
      FieldBitmapGroupSetPtr field_group_set_ptr);
  std::vector<uint64_t> label_u64_;
};

}  // namespace vectordb
