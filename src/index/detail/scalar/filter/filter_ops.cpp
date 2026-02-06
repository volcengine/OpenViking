// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#include "filter_ops.h"
#include "spdlog/spdlog.h"

namespace vectordb {

int get_json_float_value(const JsonValue& temp_val, float& value) {
  if (temp_val.IsDouble()) {
    value = temp_val.GetDouble();
  } else if (temp_val.IsInt64()) {
    value = (float)(temp_val.GetInt64());
  } else {
    SPDLOG_WARN(
        "get_json_float_value failed: expected Double or Int64, got type {}",
        static_cast<int>(temp_val.GetType()));
    return -1;
  }
  return 0;
}

int get_json_int_value(const JsonValue& temp_val, int64_t& value) {
  if (temp_val.IsDouble()) {
    value = (int64_t)(temp_val.GetDouble());
  } else if (temp_val.IsInt64()) {
    value = temp_val.GetInt64();
  } else {
    SPDLOG_WARN(
        "get_json_int_value failed: expected Double or Int64, got type {}",
        static_cast<int>(temp_val.GetType()));
    return -1;
  }
  return 0;
}

int get_json_double_value(const JsonValue& temp_val, double& value) {
  if (temp_val.IsDouble()) {
    value = temp_val.GetDouble();
  } else if (temp_val.IsInt64()) {
    value = (double)(temp_val.GetInt64());
  } else {
    SPDLOG_WARN(
        "get_json_double_value failed: expected Double or Int64, got type {}",
        static_cast<int>(temp_val.GetType()));
    return -1;
  }
  return 0;
}

using FilterOpCreator = std::function<FilterOpBasePtr()>;
using FieldOpCreator = std::function<FieldOpBasePtr()>;
using LogicOpCreator = std::function<LogicOpBasePtr()>;

const std::unordered_map<std::string, FilterOpCreator>&
get_filter_op_registry() {
  static const std::unordered_map<std::string, FilterOpCreator> kRegistry = {
      {"and", []() { return std::make_shared<AndOp>(); }},
      {"or", []() { return std::make_shared<OrOp>(); }},
      {"must", []() { return std::make_shared<MustOp>(); }},
      {"must_not", []() { return std::make_shared<MustNotOp>(); }},
      {"range", []() { return std::make_shared<RangeOp>(false); }},
      {"geo_range", []() { return std::make_shared<RangeOp>(false); }},
      {"range_out", []() { return std::make_shared<RangeOp>(true); }},
      {"label_in", []() { return std::make_shared<LabelInOp>(); }},
      {"prefix", []() { return std::make_shared<PrefixOp>(); }},
      {"contains", []() { return std::make_shared<ContainsOp>(); }},
      {"regex", []() { return std::make_shared<RegexOp>(); }}};
  return kRegistry;
}

const std::unordered_map<std::string, FieldOpCreator>& get_field_op_registry() {
  static const std::unordered_map<std::string, FieldOpCreator> kRegistry = {
      {"must", []() { return std::make_shared<MustOp>(); }},
      {"must_not", []() { return std::make_shared<MustNotOp>(); }},
      {"range", []() { return std::make_shared<RangeOp>(false); }},
      {"geo_range", []() { return std::make_shared<RangeOp>(false); }},
      {"range_out", []() { return std::make_shared<RangeOp>(true); }},
      {"prefix", []() { return std::make_shared<PrefixOp>(); }},
      {"contains", []() { return std::make_shared<ContainsOp>(); }},
      {"regex", []() { return std::make_shared<RegexOp>(); }}};
  return kRegistry;
}

const std::unordered_map<std::string, LogicOpCreator>& get_logic_op_registry() {
  static const std::unordered_map<std::string, LogicOpCreator> kRegistry = {
      {"and", []() { return std::make_shared<AndOp>(); }},
      {"or", []() { return std::make_shared<OrOp>(); }},
      {"noop", []() { return std::make_shared<Noop>(); }}};
  return kRegistry;
}

FilterOpBasePtr make_filter_op_by_opname(const std::string& opname) {
  const auto& registry = get_filter_op_registry();
  auto it = registry.find(opname);
  if (it != registry.end()) {
    return it->second();
  }
  SPDLOG_WARN(
      "Unsupported filter op '{}'. Supported ops: and, or, must, must_not, range, geo_range, range_out, label_in, prefix, contains, regex",
      opname);
  return nullptr;
}

FieldOpBasePtr make_field_op_by_opname(const std::string& opname) {
  const auto& registry = get_field_op_registry();
  auto it = registry.find(opname);
  if (it != registry.end()) {
    return it->second();
  }
  SPDLOG_WARN(
      "Unsupported field op '{}'. Supported ops: must, must_not, range, geo_range, range_out, prefix, contains, regex",
      opname);
  return nullptr;
}

LogicOpBasePtr make_logic_op_by_opname(const std::string& opname) {
  const auto& registry = get_logic_op_registry();
  auto it = registry.find(opname);
  if (it != registry.end()) {
    return it->second();
  }
  SPDLOG_WARN("Unsupported logic op '{}'. Supported ops: and, or, noop",
              opname);
  return nullptr;
}

int parse_dir_semantic_para(const JsonValue& json_doc) {
  int depth = -1;
  if (json_doc.HasMember("para")) {  // extend parameter for directory index:
                                     // only used in path_field_name
    const JsonValue& para_val = json_doc["para"];
    std::string para_str;
    if (para_val.IsString()) {
      para_str = para_val.GetString();
    } else if (para_val.IsArray()) {
      if (para_val.Size() > 0UL && para_val[0].IsString()) {
        para_str = para_val[0].GetString();
        if (para_val.Size() > 1UL) {
          SPDLOG_WARN(
              "parse_dir_semantic_para: 'para' array has multiple values, only the first one is used.");
        }
      }
    } else {
      SPDLOG_ERROR(
          "parse_dir_semantic_para: 'para' must be a string or array of strings.");
      return -2;  // Error return value
    }
    if (!para_str.empty()) {
      para_str.erase(
          std::remove_if(para_str.begin(), para_str.end(),
                         [](unsigned char c) { return std::isspace(c); }),
          para_str.end());
      if (para_str.rfind("-d=", 0) == 0) {
        try {
          depth = std::stoi(para_str.substr(3));
        } catch (const std::exception& e) {
          SPDLOG_ERROR(
              "parse_dir_semantic_para: depth_ stoi failed from para string: {}",
              para_str);
          return -2;  // Return error
        }
        depth = std::max(-1, std::min(50, depth));
      } else if (!para_str.empty()) {
        SPDLOG_WARN(
            "parse_dir_semantic_para: invalid 'para' content: [{}]. It will be ignored.",
            para_str);
      }
    }
  }
  return depth;
}

FilterOpBasePtr parse_filter_json_doc(const JsonDoc& json_doc) {
  if (!json_doc.IsObject()) {
    SPDLOG_WARN(
        "parse_filter_json_doc failed: expected JSON object, got type {}",
        static_cast<int>(json_doc.GetType()));
    return nullptr;
  }
  if (!json_doc.HasMember("op")) {
    return nullptr;
  }
  if (!json_doc["op"].IsString()) {
    SPDLOG_WARN(
        "parse_filter_json_doc failed: field 'op' must be string, got type {}",
        static_cast<int>(json_doc["op"].GetType()));
    return nullptr;
  }
  std::string opname = json_doc["op"].GetString();
  FilterOpBasePtr new_op = make_filter_op_by_opname(opname);
  if (!new_op) {
    return nullptr;
  }
  int ret = new_op->load_json_doc(json_doc);
  if (ret != 0) {
    SPDLOG_ERROR(
        "parse_filter_json_doc: op '{}' load_json_doc failed with error code {}",
        opname, ret);
    return nullptr;
  }
  if (!new_op->is_valid()) {
    SPDLOG_ERROR(
        "parse_filter_json_doc: op '{}' validation failed after loading",
        opname);
    return nullptr;
  }
  return new_op;
}

FilterOpBasePtr parse_filter_json_str(const std::string& json_str) {
  JsonDoc json_doc;
  json_doc.Parse(json_str.c_str());
  if (json_doc.HasParseError()) {
    size_t preview_len = std::min(json_str.length(), size_t(100));
    SPDLOG_WARN(
        "parse_filter_json_str failed: JSON parse error {} at offset {}. Input preview: '{}'",
        static_cast<int>(json_doc.GetParseError()),
        static_cast<int>(json_doc.GetErrorOffset()),
        json_str.substr(0, preview_len));
    return nullptr;
  }
  if (json_doc.IsObject() && json_doc.HasMember("filter")) {
    JsonDoc filter_doc;
    filter_doc.CopyFrom(json_doc["filter"], filter_doc.GetAllocator());
    return parse_filter_json_doc(filter_doc);
  }
  return parse_filter_json_doc(json_doc);
}

FilterOpBasePtr parse_filter_json_doc_outter(const JsonDoc& json_doc) {
  if (json_doc.IsObject() && json_doc.HasMember("filter")) {
    JsonDoc filter_doc;
    filter_doc.CopyFrom(json_doc["filter"], filter_doc.GetAllocator());
    return parse_filter_json_doc(filter_doc);
  }
  return parse_filter_json_doc(json_doc);
}

// LogicOpBase

int LogicOpBase::parse_conds_ops(const JsonValue& json_doc) {
  if (!json_doc.IsObject() || !json_doc.HasMember("conds")) {
    SPDLOG_WARN(
        "LogicOpBase '{}' parse_conds_ops failed: missing required field 'conds'",
        op_name());
    return -1;
  }
  const JsonValue& conds_arr = json_doc["conds"];
  if (!conds_arr.IsArray()) {
    SPDLOG_WARN(
        "LogicOpBase '{}' parse_conds_ops failed: field 'conds' must be array, got type {}",
        op_name(), static_cast<int>(conds_arr.GetType()));
    return -2;
  }
  if (conds_arr.Size() <= 0) {
    SPDLOG_WARN("LogicOpBase '{}' parse_conds_ops: empty 'conds' array",
                op_name());
    return -2;
  }
  for (rapidjson::SizeType i = 0; i < conds_arr.Size(); i++) {
    if (!conds_arr[i].IsObject()) {
      SPDLOG_WARN(
          "LogicOpBase '{}' parse_conds_ops failed: conds[{}] must be object, got type {}",
          op_name(), static_cast<int>(i),
          static_cast<int>(conds_arr[i].GetType()));
      return -3;
    }
    JsonDoc conds_i_doc;
    conds_i_doc.CopyFrom(conds_arr[i], conds_i_doc.GetAllocator());
    FilterOpBasePtr conds_i_op = parse_filter_json_doc(conds_i_doc);
    if (!conds_i_op) {
      SPDLOG_WARN(
          "LogicOpBase '{}' parse_conds_ops failed: conds[{}] parse failed",
          op_name(), static_cast<int>(i));
      return -4;
    }
    empty_conds_ |= conds_i_op->is_empty_conds();
    logic_conds_.push_back(conds_i_op);
  }
  return 0;
}

JsonDocPtr LogicOpBase::get_json_doc() {
  if (!valid_) {
    SPDLOG_WARN("LogicOpBase '{}' get_json_doc failed: operator is not valid",
                op_name());
    return nullptr;
  }
  JsonDocPtr json_ptr = std::make_shared<JsonDoc>();
  json_ptr->SetObject();
  JsonDoc::AllocatorType& allo = json_ptr->GetAllocator();
  {
    JsonValue temp_op;
    temp_op.SetString(op_name().c_str(), op_name().size(), allo);
    json_ptr->AddMember("op", temp_op, allo);
  }
  JsonValue conds(rapidjson::kArrayType);
  for (auto logic_i_ptr : logic_conds_) {
    JsonDocPtr cond_i_p = logic_i_ptr->get_json_doc();
    if (!cond_i_p) {
      return nullptr;
    }
    JsonDoc& cond_i = *cond_i_p;
    JsonValue temp;
    temp.CopyFrom(cond_i, allo);
    conds.PushBack(temp, allo);
  }
  json_ptr->AddMember("conds", conds, allo);
  if (ignore_empty_condition_) {
    json_ptr->AddMember("ignore_empty_condition", ignore_empty_condition_,
                        allo);
  }
  return json_ptr;
}

BitmapPtr LogicOpBase::calc_self_bitmap(
    FieldBitmapGroupSetPtr field_group_set_ptr) {
  BitmapPtr pres;
  if (op_name() == "and" && ignore_empty_condition_) {
    for (auto logic_i_ptr : logic_conds_) {
      BitmapPtr cond_result =
          logic_i_ptr->calc_bitmap(field_group_set_ptr, nullptr, op_name());
      if (cond_result) {
        if (pres == nullptr) {
          pres = cond_result;
          continue;
        }
        pres->Intersect(cond_result.get());
      }
    }
    return pres;
  }

  for (auto logic_i_ptr : logic_conds_) {
    pres = logic_i_ptr->calc_bitmap(field_group_set_ptr, pres, op_name());
    if (!pres) {
      if (op_name() == "or") {
        continue;
      } else if (op_name() == "and") {
        return nullptr;
      } else {
        return nullptr;
      }
    }
  }

  return pres;
}

BitmapPtr LogicOpBase::calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                                   BitmapPtr pres,
                                   const std::string on_res_op) {
  if (!pres) {
    return calc_self_bitmap(field_group_set_ptr);
  } else {
    // not tested
    if (on_res_op == "and") {
      BitmapPtr temp = calc_self_bitmap(field_group_set_ptr);
      if (!temp) {
        return nullptr;
      }
      pres->Intersect(temp.get());
    } else if (on_res_op == "or") {
      pres->Union(calc_self_bitmap(field_group_set_ptr).get());
    } else {
      return nullptr;
    }
    return pres;
  }
}

// AndOp Logical AND

int AndOp::load_json_doc(const JsonValue& json_doc) {
  int ret = parse_conds_ops(json_doc);
  if (ret != 0) {
    return ret;
  }
  if (json_doc.IsObject() && json_doc.HasMember("ignore_empty_condition")) {
    auto& ignore_conf = json_doc["ignore_empty_condition"];
    if (ignore_conf.IsBool() && ignore_conf.GetBool()) {
      set_ignore_empty_condition(true);
    }
  }
  valid_ = true;
  return 0;
}

// OrOp Logical OR

int OrOp::load_json_doc(const JsonValue& json_doc) {
  int ret = parse_conds_ops(json_doc);
  if (ret != 0) {
    return ret;
  }
  valid_ = true;
  return 0;
}

// FieldOpBase

int FieldOpBase::parse_conds_ops(const JsonValue& json_doc) {
  // field
  if (!json_doc.IsObject() || !json_doc.HasMember("field")) {
    return -1;
  }
  const JsonValue& field_val = json_doc["field"];
  if (field_val.IsString()) {
    fields_.clear();
    fields_.push_back(field_val.GetString());
  } else if (field_val.IsArray()) {
    for (rapidjson::SizeType i = 0; i < field_val.Size(); i++) {
      if (field_val[i].IsString()) {
        fields_.push_back(field_val[i].GetString());
      }
    }
    if (fields_.size() <= 0UL) {
      return -2;
    }
  } else {
    return -2;
  }
  // conds
  if (!json_doc.HasMember("conds")) {
    return -3;
  }
  const JsonValue& conds_arr = json_doc["conds"];
  if (!conds_arr.IsArray() || conds_arr.Size() <= 0) {
    empty_conds_ = true;
    return 0;
  }
  bool is_id_conds = false;
  bool is_type_conds = false;
  for (rapidjson::SizeType i = 0; i < conds_arr.Size(); i++) {
    if (conds_arr[i].IsInt64()) {
      if (is_type_conds) {
        return -5;
      }
      int64_t temp_id = conds_arr[i].GetInt64();
      id_conds_.emplace_back(temp_id);
      type_conds_.emplace_back(std::to_string(temp_id));
      is_id_conds = true;
    } else if (conds_arr[i].IsString()) {
      if (is_id_conds) {
        return -6;
      }
      type_conds_.emplace_back(std::string(conds_arr[i].GetString(),
                                           conds_arr[i].GetStringLength()));
      is_type_conds = true;
    } else if (conds_arr[i].IsBool()) {
      if (is_type_conds) {
        return -5;
      }
      bool temp_id = conds_arr[i].GetBool();
      if (temp_id) {
        id_conds_.emplace_back(1);
        type_conds_.emplace_back("1");
      } else {
        id_conds_.emplace_back(0);
        type_conds_.emplace_back("0");
      }
      is_id_conds = true;
    } else {
      return -7;
    }
  }
  return 0;
}

JsonDocPtr FieldOpBase::get_json_doc() {
  if (!valid_) {
    return nullptr;
  }
  JsonDocPtr json_ptr = std::make_shared<JsonDoc>();
  json_ptr->SetObject();
  JsonDoc::AllocatorType& allo = json_ptr->GetAllocator();
  {
    JsonValue temp_op;
    temp_op.SetString(op_name().c_str(), op_name().size(), allo);
    json_ptr->AddMember("op", temp_op, allo);
  }
  {
    if (fields_.size() == 1UL) {
      JsonValue temp_field;
      temp_field.SetString(fields_[0].c_str(), fields_[0].size(), allo);
      json_ptr->AddMember("field", temp_field, allo);
    } else {
      JsonValue temp_fields(rapidjson::kArrayType);
      for (std::string fi : fields_) {
        JsonValue temp;
        temp.SetString(fi.c_str(), fi.size(), allo);
        temp_fields.PushBack(temp, allo);
      }
      json_ptr->AddMember("field", temp_fields, allo);
    }
  }
  JsonValue conds(rapidjson::kArrayType);
  if (id_conds_.size() > 0) {
    for (int64_t logic_id_i : id_conds_) {
      conds.PushBack(JsonValue(logic_id_i).Move(), allo);
    }
  } else {
    for (std::string logic_type_i : type_conds_) {
      JsonValue temp;
      temp.SetString(logic_type_i.c_str(), logic_type_i.size(), allo);
      conds.PushBack(temp, allo);
    }
  }
  json_ptr->AddMember("conds", conds, allo);
  return json_ptr;
}

// RangeOp Continuous value range filter condition
int RangeOp::load_json_doc(const JsonValue& json_doc) {
  int ret = -1;
  valid_ = false;
  if (!json_doc.IsObject() || !json_doc.HasMember("field")) {
    return -1;
  }
  // field
  const JsonValue& field_val = json_doc["field"];
  if (field_val.IsString()) {
    fields_.clear();
    fields_.push_back(field_val.GetString());
  } else if (field_val.IsArray()) {
    for (rapidjson::SizeType i = 0; i < field_val.Size(); i++) {
      if (field_val[i].IsString()) {
        fields_.push_back(field_val[i].GetString());
      }
    }
    if (fields_.size() <= 0) {
      // Require at least one field
      return -2;
    }
  } else {
    return -2;
  }
  // center
  if (json_doc.HasMember("center")) {
    const JsonValue& center_val = json_doc["center"];
    if (center_val.IsArray()) {
      center_.resize(center_val.Size());
      if (center_.size() <= 0UL) {
        // 要求至少有一个
        // not valid");
        return -3;
      }
      for (rapidjson::SizeType i = 0; i < center_val.Size(); i++) {
        ret = get_json_double_value(center_val[i], center_[i]);
        if (ret != 0) {
          return ret;
        }
      }
    } else {
      return -3;
    }
  }
  // radius
  if (json_doc.HasMember("radius")) {
    const JsonValue& radius_val = json_doc["radius"];
    ret = get_json_double_value(radius_val, radius_);
    if (ret != 0 || radius_ < 0.0) {
      return ret;
    }
  }
  // conds
  if (fields_.size() == 1UL) {
    // gte, gt, lte, lt
    greater_than_ = -DBL_MAX;
    less_than_ = DBL_MAX;
    greater_than_equal_ = false;
    less_than_equal_ = false;
    bool has_any_condition = false;
    // parse conditions
    if (center_.size() == 1UL && radius_ >= 0.0) {
      greater_than_ = center_[0] - radius_;
      less_than_ = center_[0] + radius_;
      greater_than_equal_ = true;
      less_than_equal_ = true;
      has_any_condition = true;
    }
    if (json_doc.HasMember("gte")) {
      const JsonValue& temp_val = json_doc["gte"];
      ret = get_json_double_value(temp_val, greater_than_);
      if (ret != 0) {
        return ret;
      }
      greater_than_equal_ = true;
      has_any_condition = true;
    } else if (json_doc.HasMember("gt")) {
      const JsonValue& temp_val = json_doc["gt"];
      ret = get_json_double_value(temp_val, greater_than_);
      if (ret != 0) {
        return ret;
      }
      greater_than_equal_ = false;
      has_any_condition = true;
    }
    //
    if (json_doc.HasMember("lte")) {
      const JsonValue& temp_val = json_doc["lte"];
      ret = get_json_double_value(temp_val, less_than_);
      if (ret != 0) {
        return ret;
      }
      less_than_equal_ = true;
      has_any_condition = true;
    } else if (json_doc.HasMember("lt")) {
      const JsonValue& temp_val = json_doc["lt"];
      ret = get_json_double_value(temp_val, less_than_);
      if (ret != 0) {
        return ret;
      }
      less_than_equal_ = false;
      has_any_condition = true;
    }
    if (!has_any_condition) {
      return -4;
    }
    valid_ = true;
  } else if (fields_.size() == 2UL && center_.size() == 2UL && radius_ >= 0.0) {
    valid_ = true;
  } else {
    return -5;
  }
  return 0;
}

JsonDocPtr RangeOp::get_json_doc() {
  if (!valid_) {
    return nullptr;
  }
  JsonDocPtr json_ptr = std::make_shared<JsonDoc>();
  json_ptr->SetObject();
  JsonDoc::AllocatorType& allo = json_ptr->GetAllocator();
  {
    JsonValue temp_op;
    temp_op.SetString(op_name().c_str(), op_name().size(), allo);
    json_ptr->AddMember("op", temp_op, allo);
  }
  {
    if (fields_.size() == 1UL) {
      JsonValue temp_field;
      temp_field.SetString(fields_[0].c_str(), fields_[0].size(), allo);
      json_ptr->AddMember("field", temp_field, allo);
    } else {
      JsonValue temp_fields(rapidjson::kArrayType);
      for (std::string fi : fields_) {
        JsonValue temp;
        temp.SetString(fi.c_str(), fi.size(), allo);
        temp_fields.PushBack(temp, allo);
      }
      json_ptr->AddMember("field", temp_fields, allo);
    }
  }
  if (greater_than_ > -DBL_MAX) {
    JsonValue temp_gt;
    temp_gt.SetDouble(greater_than_);
    if (greater_than_equal_) {
      json_ptr->AddMember("gte", temp_gt, allo);
    } else {
      json_ptr->AddMember("gt", temp_gt, allo);
    }
  }
  if (less_than_ < DBL_MAX) {
    JsonValue temp_lt;
    temp_lt.SetDouble(less_than_);
    if (less_than_equal_) {
      json_ptr->AddMember("lte", temp_lt, allo);
    } else {
      json_ptr->AddMember("lt", temp_lt, allo);
    }
  }
  if (radius_ > 0.0 || center_.size() > 1UL) {
    JsonValue temp_fields(rapidjson::kArrayType);
    for (float fi : center_) {
      JsonValue temp;
      temp.SetDouble(fi);
      temp_fields.PushBack(temp, allo);
    }
    json_ptr->AddMember("center", temp_fields, allo);
    json_ptr->AddMember("radius", radius_, allo);
  }
  return json_ptr;
}

BitmapPtr RangeOp::calc_self_bitmap(
    FieldBitmapGroupSetPtr field_group_set_ptr) {
  BitmapPtr pres = nullptr;
  if (fields_.size() == 2UL && center_.size() == 2UL) {
    pres = field_group_set_ptr->make_range2d_copy(fields_, center_, radius_);
    if (!pres) {
      SPDLOG_DEBUG(
          "RangeOp::calc_self_bitmap {} {}, make_range2d_copy nullptr, radius {}",
          fields_[0], fields_[1], radius_);
      return nullptr;
    }
  } else {
    pres = field_group_set_ptr->make_range_copy(
        range_out_, fields_[0], less_than_, less_than_equal_, greater_than_,
        greater_than_equal_);
    if (!pres) {
      SPDLOG_DEBUG(
          "RangeOp::calc_self_bitmap {} make_range_copy nullptr, lt:{} e:{} gt:{} e:{}",
          fields_[0], less_than_, less_than_equal_, greater_than_,
          greater_than_equal_);
      return nullptr;
    }
  }

  return pres;
}

BitmapPtr RangeOp::calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                               BitmapPtr pres, const std::string on_res_op) {
  if (!is_valid()) {
    return nullptr;
  }
  if (!pres) {
    return calc_self_bitmap(field_group_set_ptr);
  } else {
    // has pres
    if (on_res_op == "and") {
      BitmapPtr temp = calc_self_bitmap(field_group_set_ptr);
      if (!temp) {
        return nullptr;
      }
      pres->Intersect(temp.get());
    } else if (on_res_op == "or") {
      BitmapPtr temp = calc_self_bitmap(field_group_set_ptr);
      if (!temp) {
        // OR with empty condition returns original result
        return pres;
      }
      pres->Union(temp.get());
    } else {
      return nullptr;
    }
  }
  return pres;
}

// MustOp Must match condition

int MustOp::load_json_doc(const JsonValue& json_doc) {
  int ret = parse_conds_ops(json_doc);
  if (ret != 0) {
    return ret;
  }
  depth_ = parse_dir_semantic_para(json_doc);
  if (depth_ == -2) {
    valid_ = false;
    return -1;
  }
  valid_ = true;
  return 0;
}

BitmapPtr MustOp::calc_self_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr) {
  BitmapPtr pres = nullptr;

  if (field_group_set_ptr->is_path_field_name(fields_[0])) {
    pres = field_group_set_ptr->make_path_field_copy(fields_[0], type_conds_,
                                                     depth_);
  } else {
    pres = field_group_set_ptr->make_field_copy(fields_[0], type_conds_);
  }
  return pres;
}

BitmapPtr MustOp::calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                              BitmapPtr pres, const std::string on_res_op) {
  if (type_conds_.size() <= 0) {
    return nullptr;
  }
  if (!pres) {
    return calc_self_bitmap(field_group_set_ptr);
  } else {
    // has pres
    if (on_res_op == "and") {
      BitmapPtr temp = calc_self_bitmap(field_group_set_ptr);
      if (!temp) {
        return nullptr;
      }
      pres->Intersect(temp.get());
    } else if (on_res_op == "or") {
      BitmapPtr temp = calc_self_bitmap(field_group_set_ptr);
      if (!temp) {
        // OR with empty condition returns original result
        return pres;
      }
      pres->Union(temp.get());
    } else {
      return nullptr;
    }
  }
  return pres;
}

// MustNotOp Must not match condition

int MustNotOp::load_json_doc(const JsonValue& json_doc) {
  int ret = parse_conds_ops(json_doc);
  if (ret != 0) {
    return ret;
  }
  depth_ = parse_dir_semantic_para(json_doc);
  if (depth_ == -2) {
    valid_ = false;
    return -1;
  }
  valid_ = true;
  return 0;
}

BitmapPtr MustNotOp::calc_self_bitmap(
    FieldBitmapGroupSetPtr field_group_set_ptr) {
  BitmapPtr pres = nullptr;

  if (field_group_set_ptr->is_path_field_name(fields_[0])) {
    pres = field_group_set_ptr->make_path_field_exclude_copy(
        fields_[0], type_conds_, depth_);
  } else {
    pres =
        field_group_set_ptr->make_field_exclude_copy(fields_[0], type_conds_);
  }

  return pres;
}

BitmapPtr MustNotOp::calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                                 BitmapPtr pres, const std::string on_res_op) {
  if (!pres) {
    pres = calc_self_bitmap(field_group_set_ptr);
  } else {
    // has pres
    if (on_res_op == "and") {
      if (type_conds_.size() == 1) {
        const Bitmap* temp_p =
            field_group_set_ptr->get_bitmap(fields_[0], type_conds_[0]);
        if (temp_p) {
          pres->Exclude(temp_p);
        }

      } else if (type_conds_.size() > 1) {
        BitmapPtr temp =
            field_group_set_ptr->make_field_copy(fields_[0], type_conds_);
        if (temp) {
          pres->Exclude(temp.get());
        }
      }
    } else if (on_res_op == "or") {
      BitmapPtr temp = calc_self_bitmap(field_group_set_ptr);
      if (temp) {
        // or 计算一个空条件，返回原结果
        pres->Union(temp.get());
      }
    } else {
      return nullptr;
    }
  }
  return pres;
}

JsonDocPtr LabelInOp::get_json_doc() {
  if (!valid_) {
    return nullptr;
  }
  JsonDocPtr json_ptr = std::make_shared<JsonDoc>();
  json_ptr->SetObject();
  JsonDoc::AllocatorType& allo = json_ptr->GetAllocator();
  {
    JsonValue temp_op;
    temp_op.SetString(op_name().c_str(), op_name().size(), allo);
    json_ptr->AddMember("op", temp_op, allo);
  }

  JsonValue conds(rapidjson::kArrayType);
  if (!label_u64_.empty()) {
    for (uint64_t label_i : label_u64_) {
      conds.PushBack(JsonValue(label_i).Move(), allo);
    }
  }
  json_ptr->AddMember("labels", conds, allo);
  return json_ptr;
}

int LabelInOp::load_json_doc(const JsonValue& json_doc) {
  // conds
  if (!json_doc.IsObject() || !json_doc.HasMember("labels")) {
    return -3;
  }
  const JsonValue& conds_arr = json_doc["labels"];
  if (!conds_arr.IsArray() || conds_arr.Size() <= 0) {
    return -4;
  }
  bool is_uint64 = false;
  for (rapidjson::SizeType i = 0; i < conds_arr.Size(); i++) {
    if (conds_arr[i].IsUint64()) {
      uint64_t temp_id = conds_arr[i].GetUint64();
      label_u64_.emplace_back(temp_id);
      is_uint64 = true;
    } else if (conds_arr[i].IsInt64()) {
      int64_t temp_id = conds_arr[i].GetInt64();
      label_u64_.emplace_back(static_cast<uint64_t>(temp_id));
      is_uint64 = true;
    } else {
      return -8;
    }
  }
  valid_ = true;
  return 0;
}

BitmapPtr LabelInOp::calc_self_bitmap(
    FieldBitmapGroupSetPtr field_group_set_ptr) {
  BitmapPtr pres = std::make_shared<Bitmap>();
  std::vector<uint32_t> offsets;
  if (!label_u64_.empty()) {
    try {
      if (!field_group_set_ptr->convert_label_u64_to_offset(label_u64_,
                                                            offsets)) {
        return nullptr;
      }
    } catch (const std::exception& e) {
      SPDLOG_ERROR("LabelInOp: convert_label_u64_to_offset exception: {}",
                   e.what());
      return nullptr;
    } catch (...) {
      SPDLOG_ERROR("LabelInOp: convert_label_u64_to_offset unknown exception");
      return nullptr;
    }
  }
  if (offsets.empty()) {
    return nullptr;
  }
  pres->SetMany(offsets);
  return pres;
}

BitmapPtr LabelInOp::calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                                 BitmapPtr pres, const std::string on_res_op) {
  if (label_u64_.size() <= 0) {
    return nullptr;
  }
  if (!pres) {
    return calc_self_bitmap(field_group_set_ptr);
  } else {
    // has pres
    if (on_res_op == "and") {
      BitmapPtr temp = calc_self_bitmap(field_group_set_ptr);
      if (!temp) {
        return nullptr;
      }
      pres->Intersect(temp.get());
    } else if (on_res_op == "or") {
      BitmapPtr temp = calc_self_bitmap(field_group_set_ptr);
      if (!temp) {
        // OR with empty condition returns original result
        return pres;
      }
      pres->Union(temp.get());
    } else {
      return nullptr;
    }
  }
  return pres;
}

// PrefixOp Prefix match condition

int PrefixOp::load_json_doc(const JsonValue& json_doc) {
  // field
  if (!json_doc.IsObject() || !json_doc.HasMember("field")) {
    return -1;
  }
  const JsonValue& field_val = json_doc["field"];
  if (!field_val.IsString()) {
    return -2;
  }
  fields_.clear();
  fields_.push_back(field_val.GetString());

  // prefix value
  if (!json_doc.HasMember("prefix")) {
    return -3;
  }
  const JsonValue& prefix_val = json_doc["prefix"];
  if (!prefix_val.IsString()) {
    return -4;
  }
  prefix_value_ = prefix_val.GetString();
  valid_ = true;
  return 0;
}

BitmapPtr PrefixOp::calc_self_bitmap(
    FieldBitmapGroupSetPtr field_group_set_ptr) {
  BitmapPtr pres =
      field_group_set_ptr->make_field_prefix_copy(fields_[0], prefix_value_);
  if (!pres) {
    return nullptr;
  }
  return pres;
}

BitmapPtr PrefixOp::calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                                BitmapPtr pres, const std::string on_res_op) {
  if (prefix_value_.empty()) {
    return nullptr;
  }
  if (!pres) {
    return calc_self_bitmap(field_group_set_ptr);
  } else {
    // has pres
    if (on_res_op == "and") {
      BitmapPtr temp = calc_self_bitmap(field_group_set_ptr);
      if (!temp) {
        return nullptr;
      }
      pres->Intersect(temp.get());
    } else if (on_res_op == "or") {
      BitmapPtr temp = calc_self_bitmap(field_group_set_ptr);
      if (!temp) {
        // OR with empty condition returns original result
        return pres;
      }
      pres->Union(temp.get());
    } else {
      return nullptr;
    }
  }
  return pres;
}

// ContainsOp Contains match condition

int ContainsOp::load_json_doc(const JsonValue& json_doc) {
  // field
  if (!json_doc.IsObject() || !json_doc.HasMember("field")) {
    return -1;
  }
  const JsonValue& field_val = json_doc["field"];
  if (!field_val.IsString()) {
    return -2;
  }
  fields_.clear();
  fields_.push_back(field_val.GetString());

  // substring value
  if (!json_doc.HasMember("substring")) {
    return -3;
  }
  const JsonValue& substring_val = json_doc["substring"];
  if (!substring_val.IsString()) {
    return -4;
  }
  substring_value_ = substring_val.GetString();
  valid_ = true;
  return 0;
}

BitmapPtr ContainsOp::calc_self_bitmap(
    FieldBitmapGroupSetPtr field_group_set_ptr) {
  BitmapPtr pres = field_group_set_ptr->make_field_contains_copy(
      fields_[0], substring_value_);
  if (!pres) {
    return nullptr;
  }
  return pres;
}

BitmapPtr ContainsOp::calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                                  BitmapPtr pres, const std::string on_res_op) {
  if (substring_value_.empty()) {
    return nullptr;
  }
  if (!pres) {
    return calc_self_bitmap(field_group_set_ptr);
  } else {
    // has pres
    if (on_res_op == "and") {
      BitmapPtr temp = calc_self_bitmap(field_group_set_ptr);
      if (!temp) {
        return nullptr;
      }
      pres->Intersect(temp.get());
    } else if (on_res_op == "or") {
      BitmapPtr temp = calc_self_bitmap(field_group_set_ptr);
      if (!temp) {
        // OR with empty condition returns original result
        return pres;
      }
      pres->Union(temp.get());
    } else {
      return nullptr;
    }
  }
  return pres;
}

// RegexOp Regex match condition

int RegexOp::load_json_doc(const JsonValue& json_doc) {
  // field
  if (!json_doc.IsObject() || !json_doc.HasMember("field")) {
    return -1;
  }
  const JsonValue& field_val = json_doc["field"];
  if (!field_val.IsString()) {
    return -2;
  }
  fields_.clear();
  fields_.push_back(field_val.GetString());

  // pattern value
  if (!json_doc.HasMember("pattern")) {
    return -3;
  }
  const JsonValue& pattern_val = json_doc["pattern"];
  if (!pattern_val.IsString()) {
    return -4;
  }
  pattern_value_ = pattern_val.GetString();
  valid_ = true;
  return 0;
}

BitmapPtr RegexOp::calc_self_bitmap(
    FieldBitmapGroupSetPtr field_group_set_ptr) {
  BitmapPtr pres =
      field_group_set_ptr->make_field_regex_copy(fields_[0], pattern_value_);
  if (!pres) {
    return nullptr;
  }
  return pres;
}

BitmapPtr RegexOp::calc_bitmap(FieldBitmapGroupSetPtr field_group_set_ptr,
                               BitmapPtr pres, const std::string on_res_op) {
  if (pattern_value_.empty()) {
    return nullptr;
  }
  if (!pres) {
    return calc_self_bitmap(field_group_set_ptr);
  } else {
    // has pres
    if (on_res_op == "and") {
      BitmapPtr temp = calc_self_bitmap(field_group_set_ptr);
      if (!temp) {
        return nullptr;
      }
      pres->Intersect(temp.get());
    } else if (on_res_op == "or") {
      BitmapPtr temp = calc_self_bitmap(field_group_set_ptr);
      if (!temp) {
        // OR with empty condition returns original result
        return pres;
      }
      pres->Union(temp.get());
    } else {
      return nullptr;
    }
  }
  return pres;
}

}  // namespace vectordb
