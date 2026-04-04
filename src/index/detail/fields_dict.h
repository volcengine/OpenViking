// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include <iostream>
#include <sstream>
#include <string>
#include <unordered_map>
#include "spdlog/spdlog.h"
#include "rapidjson/document.h"

namespace vectordb {

struct FieldsDict {
  std::unordered_map<std::string, std::string> str_kv_map_;
  std::unordered_map<std::string, double> dbl_kv_map_;

  bool empty() const {
    return str_kv_map_.empty() && dbl_kv_map_.empty();
  }

  size_t size() const {
    return str_kv_map_.size() + dbl_kv_map_.size();
  }

  std::string to_string() const {
    std::stringstream ss;
    for (const auto& item : str_kv_map_) {
      ss << item.first << "=" << item.second << ", ";
    }
    for (const auto& item : dbl_kv_map_) {
      ss << item.first << "=" << std::to_string(item.second) << ", ";
    }
    return ss.str();
  }

  int parse_from_json(const std::string& json) {
    if (json.empty()) {
      return 1;
    }
    rapidjson::Document doc;
    doc.Parse(json.c_str());

    if (doc.HasParseError()) {
      SPDLOG_ERROR("doc HasParseError json: {}", json.c_str());
      return 1;
    }
    for (rapidjson::Value::ConstMemberIterator it = doc.MemberBegin();
         it != doc.MemberEnd(); ++it) {
      std::string key = it->name.GetString();
      const rapidjson::Value& val = it->value;
      if (val.IsInt64()) {
        str_kv_map_[key] = std::to_string(val.GetInt64());
        dbl_kv_map_[key] = double(val.GetInt64());
      } else if (val.IsDouble()) {
        dbl_kv_map_[key] = val.GetDouble();
      } else if (val.IsString()) {
        str_kv_map_[key] = val.GetString();
      } else if (val.IsBool()) {
        str_kv_map_[key] = std::to_string(val.GetBool() == true);
      } else if (val.IsArray()) {
        std::stringstream ss;
        for (rapidjson::SizeType i = 0; i < val.Size(); ++i) {
          const rapidjson::Value& sub_val = val[i];
          if (i > 0) {
            ss << ";";
          }
          if (sub_val.IsInt64()) {
            ss << std::to_string(sub_val.GetInt64());
          } else if (sub_val.IsString()) {
            ss << sub_val.GetString();
          }
        }
        str_kv_map_[key] = ss.str();
      }
    }
    return 0;
  }
};

}  // namespace vectordb