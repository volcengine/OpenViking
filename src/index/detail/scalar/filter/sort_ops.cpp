// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#include "sort_ops.h"
#include "spdlog/spdlog.h"

#include <sstream>

namespace vectordb {

using SorterOpCreator = std::function<SorterOpBasePtr()>;

const std::unordered_map<std::string, SorterOpCreator>&
get_sorter_op_registry() {
  static const std::unordered_map<std::string, SorterOpCreator> kRegistry = {
      {"sort", []() { return std::make_shared<SorterOp>(); }},
      {"count", []() { return std::make_shared<CounterOp>(); }}};
  return kRegistry;
}

SorterOpBasePtr make_sorter_op_by_opname(const std::string& opname) {
  const auto& registry = get_sorter_op_registry();
  auto it = registry.find(opname);
  if (it != registry.end()) {
    return it->second();
  }
  SPDLOG_ERROR("make_sorter_op_by_opname failed: not support op[{}]", opname);
  return nullptr;
}

SorterOpBasePtr parse_sorter_json_doc(const JsonDoc& json_doc) {
  if (!json_doc.HasMember("op") || !json_doc["op"].IsString()) {
    SPDLOG_ERROR("parse_sorter_json_doc parse failed: need op");
    return nullptr;
  }
  std::string opname = json_doc["op"].GetString();
  SorterOpBasePtr new_op = make_sorter_op_by_opname(opname);
  if (!new_op) {
    SPDLOG_ERROR("make_sorter_op_by_opname unknown opname {}", opname);
    return nullptr;
  }
  int ret = new_op->load_json_doc(json_doc);
  if (ret != 0) {
    SPDLOG_ERROR("parse_sorter_json_doc load_json_doc failed: ret {}", ret);
    return nullptr;
  }
  if (!new_op->is_valid()) {
    SPDLOG_ERROR("parse_sorter_json_doc new_op not valid");
    return nullptr;
  }
  return new_op;
}

SorterOpBasePtr parse_sorter_json_str(const std::string& json_str) {
  JsonDoc json_doc;
  json_doc.Parse(json_str.c_str());
  if (json_doc.HasParseError()) {
    SPDLOG_ERROR("parse_sorter_json_str parse failed: ({}:{}) {}",
                 (int)(json_doc.GetParseError()),
                 (int)(json_doc.GetErrorOffset()),
                 rapidjson::GetParseError_En(json_doc.GetParseError()));
    return nullptr;
  }
  if (json_doc.IsObject() && json_doc.HasMember("counter")) {
    JsonDoc counter_doc;
    counter_doc.CopyFrom(json_doc["counter"], counter_doc.GetAllocator());
    return parse_sorter_json_doc(counter_doc);
  }
  if (json_doc.IsObject() && json_doc.HasMember("sorter")) {
    JsonDoc sorter_doc;
    sorter_doc.CopyFrom(json_doc["sorter"], sorter_doc.GetAllocator());
    return parse_sorter_json_doc(sorter_doc);
  }
  return parse_sorter_json_doc(json_doc);
}

SorterOpBasePtr parse_sorter_json_doc_outter(const JsonDoc& json_doc) {
  if (json_doc.IsObject() && json_doc.HasMember("counter")) {
    JsonDoc counter_doc;
    counter_doc.CopyFrom(json_doc["counter"], counter_doc.GetAllocator());
    return parse_sorter_json_doc(counter_doc);
  }
  if (json_doc.IsObject() && json_doc.HasMember("sorter")) {
    JsonDoc sorter_doc;
    sorter_doc.CopyFrom(json_doc["sorter"], sorter_doc.GetAllocator());
    return parse_sorter_json_doc(sorter_doc);
  }
  return parse_sorter_json_doc(json_doc);
}

// SorterOp
int SorterOp::load_json_doc(const JsonValue& json_doc) {
  int ret = 0;
  valid_ = false;
  std::vector<double> centers;
  ret = load_json_doc_load_fields(json_doc, fields_);
  if (ret != 0) {
    return ret;
  }
  ret = load_json_doc_load_order_ascs(json_doc, order_ascs_);
  if (ret != 0) {
    return ret;
  }
  ret = load_json_doc_load_centers(json_doc, centers);
  if (ret != 0) {
    return ret;
  }
  ret = load_json_doc_load_topk(json_doc, topk_);
  if (ret != 0) {
    return ret;
  }
  ret = load_json_doc_load_type(json_doc, type_);
  if (ret != 0) {
    return ret;
  }
  ret = load_json_doc_validate(json_doc, centers);
  if (ret != 0) {
    return ret;
  }
  valid_ = true;
  return 0;
}

int SorterOp::load_json_doc_load_fields(const JsonValue& json_doc,
                                        std::vector<std::string>& fields) {
  // field
  fields.clear();
  if (json_doc.IsObject() && json_doc.HasMember("field")) {
    const JsonValue& field_val = json_doc["field"];
    if (field_val.IsString()) {
      fields.push_back(field_val.GetString());
    } else if (field_val.IsArray()) {
      for (rapidjson::SizeType i = 0; i < field_val.Size(); i++) {
        if (!field_val[i].IsString()) {
          SPDLOG_ERROR(
              "SorterOp::load_json_doc_load_fields parse failed: field array item type not valid");
          return -101;
        }
        fields.push_back(field_val[i].GetString());
      }
      if (fields.size() <= 0) {
        // 要求至少有一个
        SPDLOG_ERROR(
            "SorterOp::load_json_doc_load_fields parse failed: field array size not valid");
        return -102;
      }
    } else {
      SPDLOG_ERROR(
          "SorterOp::load_json_doc_load_fields parse failed: field not valid");
      return -103;
    }
  } else {
    SPDLOG_ERROR(
        "SorterOp::load_json_doc_load_fields parse failed: need field");
    return -104;
  }
  return 0;
}

int SorterOp::load_json_doc_load_order_ascs(const JsonValue& json_doc,
                                            std::vector<bool>& order_ascs) {
  // order
  order_ascs.clear();
  if (json_doc.IsObject() && json_doc.HasMember("order")) {
    const JsonValue& order_val = json_doc["order"];
    if (order_val.IsString()) {
      std::string order_str(order_val.GetString());
      if (order_str == kOrderDescStr) {
        order_ascs.emplace_back(false);
      } else if (order_str == kOrderAscStr) {
        order_ascs.emplace_back(true);
      } else {
        SPDLOG_ERROR(
            "SorterOp::load_json_doc_load_order_ascs parse failed: order value not valid {}",
            order_str);
        return -201;
      }
    } else if (order_val.IsArray()) {
      for (rapidjson::SizeType i = 0; i < order_val.Size(); i++) {
        if (!order_val[i].IsString()) {
          SPDLOG_ERROR(
              "SorterOp::load_json_doc_load_order_ascs parse failed: order array item type not valid");
          return -202;
        }
        std::string order_str(order_val[i].GetString());
        if (order_str == kOrderDescStr) {
          order_ascs.emplace_back(false);
        } else if (order_str == kOrderAscStr) {
          order_ascs.emplace_back(true);
        } else {
          SPDLOG_ERROR(
              "SorterOp::load_json_doc_load_order_ascs parse failed: order array item value not valid {}",
              order_str);
          return -203;
        }
      }
    } else {
      SPDLOG_ERROR(
          "SorterOp::load_json_doc_load_order_ascs parse failed: field not valid");
      return -204;
    }
  }
  return 0;
}

int SorterOp::load_json_doc_load_centers(const JsonValue& json_doc,
                                         std::vector<double>& centers) {
  // center
  centers.clear();
  if (json_doc.IsObject() && json_doc.HasMember("center")) {
    const JsonValue& center_val = json_doc["center"];
    if (center_val.IsDouble()) {
      centers.push_back(center_val.GetDouble());
    } else if (center_val.IsArray()) {
      for (rapidjson::SizeType i = 0; i < center_val.Size(); i++) {
        if (!center_val[i].IsDouble()) {
          SPDLOG_ERROR(
              "SorterOp::load_json_doc_load_centers parse failed: center array item type not valid");
          return -301;
        }
        centers.push_back(center_val[i].GetDouble());
      }
    } else {
      SPDLOG_ERROR(
          "SorterOp::load_json_doc_load_centers parse failed: center not valid");
      return -302;
    }
  }
  return 0;
}

int SorterOp::load_json_doc_load_topk(const JsonValue& json_doc, int& topk) {
  // topk
  if (json_doc.IsObject() && json_doc.HasMember("topk")) {
    const JsonValue& topk_val = json_doc["topk"];
    if (!topk_val.IsInt()) {
      SPDLOG_ERROR(
          "SorterOp::load_json_doc_load_topk parse failed: topk value not valid");
      return -401;
    }
    topk = topk_val.GetInt();
  } else {
    topk = 0;  // Allow setting via set_topk
  }
  return 0;
}

int SorterOp::load_json_doc_load_type(const JsonValue& json_doc,
                                      SorterOpType& type) {
  // type
  if (json_doc.IsObject() && json_doc.HasMember("type")) {
    const JsonValue& type_val = json_doc["type"];
    if (!type_val.IsString()) {
      SPDLOG_ERROR(
          "SorterOp::load_json_doc_load_type parse failed: type_val must be a string");
      return -501;
    }
    std::string type_str(type_val.GetString());
    if (type_str == kTypeCenter1d) {
      type = SorterOpType::SORT_CENTER_1D;
    } else {
      SPDLOG_ERROR(
          "SorterOp::load_json_doc_load_type parse failed: type value not valid {}",
          type_str);
      return -502;
    }
  } else {
    // type: single1d
    // type: multi_single1d
    type = SorterOpType::SORT_SINGLE_1D;
  }
  return 0;
}

int SorterOp::load_json_doc_validate(const JsonValue& json_doc,
                                     std::vector<double>& centers) {
  // type
  if (type_ == SorterOpType::SORT_CENTER_1D) {
    // type: center1d
    // field check
    if (fields_.size() != 1) {
      SPDLOG_ERROR(
          "SorterOp::load_json_doc_validate parse failed: field size must be 1 in center1d type");
      return -601;
    }
    // order check
    if (order_ascs_.size() == 0) {
      order_ascs_.emplace_back(true);
    } else if (order_ascs_.size() > 1) {
      SPDLOG_ERROR(
          "SorterOp::load_json_doc_validate parse failed: order size must be 0 or 1 in center1d type");
      return -602;
    }
    if (!order_ascs_[0]) {
      SPDLOG_ERROR(
          "SorterOp::load_json_doc_validate parse failed: only support asc now in center1d type");
      return -603;
    }
    // center check
    if (centers.size() != 1) {
      SPDLOG_ERROR(
          "SorterOp::load_json_doc_validate parse failed: must have center value as one double in center1d type");
      return -604;
    }
    center1d_ = centers[0];
  } else if (fields_.size() == 1) {
    // type: single1d
    type_ = SorterOpType::SORT_SINGLE_1D;
    // order check
    if (order_ascs_.size() == 0) {
      order_ascs_.emplace_back(false);
    } else if (order_ascs_.size() > 1) {
      SPDLOG_ERROR(
          "SorterOp::load_json_doc_validate parse failed: order array size not valid");
      return -610;
    }
  } else {
    // type: multi_single1d
    type_ = SorterOpType::SORT_MULTI_SINGLE_1D;
    // order check
    if (order_ascs_.size() == 0) {
      order_ascs_.insert(order_ascs_.end(), fields_.size(), false);
    } else if (order_ascs_.size() == 1) {
      bool temp_order_asc = order_ascs_.back();
      order_ascs_.clear();
      order_ascs_.insert(order_ascs_.end(), fields_.size(), temp_order_asc);
    } else if (order_ascs_.size() != fields_.size()) {
      SPDLOG_ERROR(
          "SorterOp::load_json_doc_validate parse failed: order array size not valid");
      return -611;
    }
  }
  return 0;
}

JsonDocPtr SorterOp::get_json_doc() {
  if (!valid_) {
    SPDLOG_ERROR("SorterOp::get_json_doc failed: not valid");
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
      JsonValue temp_fields;
      temp_fields.SetArray();
      for (auto field_item : fields_) {
        JsonValue temp_field;
        temp_field.SetString(field_item.c_str(), field_item.size(), allo);
        temp_fields.PushBack(temp_field, allo);
      }
      json_ptr->AddMember("field", temp_fields, allo);
    }
  }
  {
    if (type_ == SorterOpType::SORT_CENTER_1D) {
      // center1d
      JsonValue temp_type;
      temp_type.SetString(kTypeCenter1d.c_str(), kTypeCenter1d.size(), allo);
      json_ptr->AddMember("type", temp_type, allo);
      JsonValue temp_center1d;
      temp_center1d.SetDouble(center1d_);
      json_ptr->AddMember("center", temp_center1d, allo);
    } else if (type_ != SorterOpType::SORT_SINGLE_1D &&
               type_ != SorterOpType::SORT_MULTI_SINGLE_1D) {
      SPDLOG_ERROR("SorterOp::get_json_doc failed: SorterOpType not valid");
      return nullptr;
    }
    if (order_ascs_.size() == 1UL) {
      JsonValue temp_order;
      if (order_ascs_[0]) {
        temp_order.SetString(kOrderAscStr.c_str(), kOrderAscStr.size(), allo);
      } else {
        temp_order.SetString(kOrderDescStr.c_str(), kOrderDescStr.size(), allo);
      }
      json_ptr->AddMember("order", temp_order, allo);
    } else {
      JsonValue temp_orders;
      temp_orders.SetArray();
      for (auto order_item : order_ascs_) {
        JsonValue temp_order;
        if (order_item) {
          temp_order.SetString(kOrderAscStr.c_str(), kOrderAscStr.size(), allo);
        } else {
          temp_order.SetString(kOrderDescStr.c_str(), kOrderDescStr.size(),
                               allo);
        }
        temp_orders.PushBack(temp_order, allo);
      }
      json_ptr->AddMember("order", temp_orders, allo);
    }
  }
  if (topk_ > 0) {
    JsonValue temp_topk;
    temp_topk.SetInt64(topk_);
    json_ptr->AddMember("topk", temp_topk, allo);
  }
  return json_ptr;
}

bool SorterOp::_is_small_ratio_bitmap(
    FieldBitmapGroupSetPtr field_group_set_ptr, uint32_t valid_size) {
  if (valid_size < 10000) {
    return true;
  }
  double ratio =
      (double)valid_size / (double)(1 + field_group_set_ptr->element_size());
  if (ratio < 0.005) {
    return true;
  }
  return false;
}

RecallResultPtr SorterOp::calc_topk_result(
    FieldBitmapGroupSetPtr field_group_set_ptr, BitmapPtr valid_bitmap,
    offset_filter_t filter) {
  if (!valid_bitmap) {
    return calc_topk_result(field_group_set_ptr, filter);
  }
  offset_filter_t filter_func;
  filter_func = [valid_bitmap, filter](uint32_t offset) -> bool {
    if (!valid_bitmap->Isset(offset)) {
      return true;
    }
    return filter(offset);
  };
  if (_is_small_ratio_bitmap(field_group_set_ptr,
                             valid_bitmap->get_cached_nbit())) {
    return _calc_topk_result_with_small_bitmap(field_group_set_ptr,
                                               valid_bitmap, filter_func);
  }
  return calc_topk_result(field_group_set_ptr, filter_func);
}

RecallResultPtr SorterOp::calc_topk_result(
    FieldBitmapGroupSetPtr field_group_set_ptr, BitmapPtr valid_bitmap) {
  offset_filter_t filter_func;
  if (!valid_bitmap) {
    filter_func = [](uint32_t offset) -> bool { return false; };
  } else {
    filter_func = [valid_bitmap](uint32_t offset) -> bool {
      return !valid_bitmap->Isset(offset);
    };
    if (_is_small_ratio_bitmap(field_group_set_ptr,
                               valid_bitmap->get_cached_nbit())) {
      return _calc_topk_result_with_small_bitmap(field_group_set_ptr,
                                                 valid_bitmap, filter_func);
    }
  }
  return calc_topk_result(field_group_set_ptr, filter_func);
}

RecallResultPtr SorterOp::calc_topk_result(
    FieldBitmapGroupSetPtr field_group_set_ptr, offset_filter_t filter) {
  if (!valid_) {
    SPDLOG_ERROR("SorterOp::calc_topk_result failed, op not valid");
    return nullptr;
  }
  RecallResultPtr res;

  switch (type_) {
    case SorterOpType::SORT_SINGLE_1D:
      if (field_group_set_ptr->find_field_group(fields_[0]) == nullptr) {
        // TODO: use iter
      }
      res = field_group_set_ptr->get_topk_result(fields_[0], topk_,
                                                 order_ascs_[0], filter);
      break;
    case SorterOpType::SORT_MULTI_SINGLE_1D:
      res = field_group_set_ptr->get_topk_result_with_conditions(
          fields_, topk_, order_ascs_, filter);
      break;
    case SorterOpType::SORT_CENTER_1D:
      res = field_group_set_ptr->get_topk_result_center1d(
          fields_[0], topk_, order_ascs_[0], center1d_, filter);
      break;
    default:
      SPDLOG_ERROR(
          "SorterOp::calc_topk_result: failed, SorterOpType not valid, values {}",
          static_cast<int>(type_));
      return nullptr;
  }

  return res;
}

RecallResultPtr SorterOp::_calc_topk_result_with_small_bitmap(
    FieldBitmapGroupSetPtr field_group_set_ptr, BitmapPtr valid_bitmap,
    offset_filter_t filter) {
  std::vector<uint32_t> all_valid_offset;
  valid_bitmap->get_set_list(all_valid_offset);
  uint32_t all_valid_size = all_valid_offset.size();
  uint32_t search_k = std::max((uint32_t)1, (uint32_t)topk_);
  const bool has_filter = (bool)(filter);

  std::vector<uint32_t> collected_offsets;
  collected_offsets.reserve(search_k);

  std::vector<float> collected_scores;
  collected_scores.reserve(search_k);

  std::vector<std::pair<RangedMapPtr, bool>> conditions;
  for (size_t i = 0; i < fields_.size(); i++) {
    RangedMapPtr ranged_map =
        field_group_set_ptr->get_rangedmap_ptr(fields_[i]);
    if (ranged_map == nullptr) {
      SPDLOG_ERROR(
          "SorterOp::_calc_topk_result_with_small_bitmap get_rangedmap_ptr failed, {} not exist",
          fields_[i]);
      return nullptr;
    }
    conditions.emplace_back(ranged_map, order_ascs_[i]);
  }
  if (has_filter) {
    std::vector<uint32_t> filtered_valid_offset;
    filtered_valid_offset.reserve(all_valid_size);
    for (const uint32_t& offset_i : all_valid_offset) {
      if (!filter(offset_i)) {
        filtered_valid_offset.emplace_back(offset_i);
      }
    }
    all_valid_offset = std::move(filtered_valid_offset);
    all_valid_size = all_valid_offset.size();
  }
  search_k = std::min(search_k, all_valid_size);

  std::function<bool(uint32_t&, uint32_t&)> cond_func;
  switch (type_) {
    case SorterOpType::SORT_SINGLE_1D:
    case SorterOpType::SORT_MULTI_SINGLE_1D:
      cond_func = [&](uint32_t& idx_l, uint32_t& idx_r) -> bool {
        for (std::pair<RangedMapPtr, bool>& condition : conditions) {
          double value_l = condition.first->get_score_by_offset(idx_l);
          double value_r = condition.first->get_score_by_offset(idx_r);
          const double eps = 1e-9;
          const double diff = value_l - value_r;
          if (diff > eps || diff < -eps) {
            return (value_l < value_r) ^ condition.second;
          }
        }
        return false;
      };
      break;
    case SorterOpType::SORT_CENTER_1D:
      cond_func = [&](uint32_t& idx_l, uint32_t& idx_r) -> bool {
        double value_l = conditions[0].first->get_score_by_offset(idx_l);
        double value_r = conditions[0].first->get_score_by_offset(idx_r);
        if (std::abs(value_l - center1d_) != std::abs(value_r - center1d_)) {
          return (std::abs(value_l - center1d_) >
                  std::abs(value_r - center1d_)) ^
                 conditions[0].second;
        }
        return value_l > value_r;
      };
      break;
    default:
      SPDLOG_ERROR(
          "SorterOp::_calc_topk_result_with_small_bitmap: failed, SorterOpType not valid, values {}",
          static_cast<int>(type_));
      return nullptr;
      break;
  }

  std::priority_queue<uint32_t, std::vector<uint32_t>,
                      std::function<bool(uint32_t&, uint32_t&)>>
      que(cond_func, std::move(all_valid_offset));
  for (uint32_t j = 0; j < search_k; j++) {
    uint32_t offset = que.top();
    que.pop();
    collected_scores.emplace_back(
        conditions[0].first->get_score_by_offset(offset));
    collected_offsets.emplace_back(offset);
  }

  RecallResultPtr res_ptr = std::make_shared<RecallResult>();
  if (res_ptr->swap_offsets_vec(collected_scores, collected_offsets) != 0) {
    return nullptr;
  }

  SPDLOG_DEBUG("SorterOp::_calc_topk_result_with_small_bitmap topk {} in {}",
              search_k, all_valid_size);
  return res_ptr;
}

int CounterOp::load_json_doc(const JsonValue& json_doc) {
  int ret = 0;
  if (json_doc.IsObject() && json_doc.HasMember("field")) {
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
        SPDLOG_ERROR(
            "SorterOp::parse_conds_ops parse failed: field array not valid");
        return -2;
      }
    } else {
      SPDLOG_ERROR("SorterOp::parse_conds_ops parse failed: field not valid");
      return -3;
    }
  }
  if (json_doc.IsObject() && json_doc.HasMember("gt")) {
    const JsonValue& gt_val = json_doc["gt"];
    int64_t temp_gt = 0;
    ret = get_json_int_value(gt_val, temp_gt);
    if (ret != 0) {
      SPDLOG_ERROR("CounterOp::get_json_doc get_json_int_value gt failed");
      return ret;
    }
    gt_ = (int)temp_gt;
  } else {
    gt_ = -1;
  }
  if (json_doc.IsObject() && json_doc.HasMember("max_entry")) {
    const JsonValue& max_entry_val = json_doc["max_entry"];
    int64_t temp_max_entry = 0;
    ret = get_json_int_value(max_entry_val, temp_max_entry);
    if (ret != 0) {
      SPDLOG_ERROR(
          "CounterOp::get_json_doc get_json_int_value max_entry failed");
      return ret;
    }
    max_entry_ = (int)temp_max_entry;
  } else {
    max_entry_ = 10000;
  }
  return 0;
}

JsonDocPtr CounterOp::get_json_doc() {
  if (!valid_) {
    SPDLOG_ERROR("CounterOp::get_json_doc failed: not valid");
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
  if (gt_ > 0) {
    JsonValue temp_op;
    temp_op.SetInt64(gt_);
    json_ptr->AddMember("gt", temp_op, allo);
  }
  if (max_entry_ > 0) {
    JsonValue temp_op;
    temp_op.SetInt64(max_entry_);
    json_ptr->AddMember("max_entry", temp_op, allo);
  }
  {
    if (fields_.size() == 1UL) {
      JsonValue temp_field;
      temp_field.SetString(fields_[0].c_str(), fields_[0].size(), allo);
      json_ptr->AddMember("field", temp_field, allo);
    } else if (fields_.size() > 1UL) {
      JsonValue temp_fields(rapidjson::kArrayType);
      for (std::string fi : fields_) {
        JsonValue temp;
        temp.SetString(fi.c_str(), fi.size(), allo);
        temp_fields.PushBack(temp, allo);
      }
      json_ptr->AddMember("field", temp_fields, allo);
    }
  }
  return json_ptr;
}

RecallResultPtr CounterOp::_calc_topk_result(
    FieldBitmapGroupSetPtr field_group_set_ptr, BitmapPtr valid_bitmap) {
  if (fields_.size() > 2UL) {
    SPDLOG_ERROR("CounterOp::_calc_topk_result support no more than 2 fields");
    return nullptr;
  }
  if (fields_.size() == 0) {
    size_t count_value;
    if (valid_bitmap == nullptr) {
      count_value = field_group_set_ptr->element_size();
    } else {
      count_value = valid_bitmap->get_cached_nbit();
    }
    auto res_ptr = std::make_shared<RecallResult>();
    // set total count in json
    JsonDocPtr json_ptr = std::make_shared<JsonDoc>();
    json_ptr->SetObject();
    JsonDoc::AllocatorType& allo = json_ptr->GetAllocator();
    JsonValue key;
    JsonValue value;
    key.SetString("__total_count__", sizeof("__total_count__") - 1, allo);
    value.SetInt64((int64_t)count_value);
    json_ptr->AddMember(key, value, allo);
    res_ptr->merge_dsl_op_extra_json(std::move(*json_ptr));

    return res_ptr;
  }
  // count by fields
  std::map<std::string, uint32_t> enum_count;
  int ret =
      field_group_set_ptr->count_field_enums(fields_, enum_count, valid_bitmap);
  if (ret != 0) {
    SPDLOG_ERROR("CounterOp::_calc_topk_result count_field_enums ret {}", ret);
    return nullptr;
  }
  // write to json
  auto res_ptr = std::make_shared<RecallResult>();
  if (!enum_count.empty()) {
    JsonDocPtr json_ptr = std::make_shared<JsonDoc>();
    json_ptr->SetObject();
    JsonDoc::AllocatorType& allo = json_ptr->GetAllocator();
    int entry_num = 0;
    for (const auto& kv : enum_count) {
      if ((int)kv.second > gt_) {
        JsonValue key;
        JsonValue value;
        key.SetString(kv.first.c_str(), kv.first.size(), allo);
        value.SetInt64((int64_t)kv.second);
        json_ptr->AddMember(key, value, allo);
        entry_num++;
        if (max_entry_ > 0 && entry_num >= max_entry_) {
          break;
        }
      }
    }
    res_ptr->merge_dsl_op_extra_json(std::move(*json_ptr));
  }

  return res_ptr;
}

}  // namespace vectordb
