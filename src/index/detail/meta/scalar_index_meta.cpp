// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include "scalar_index_meta.h"
#include <fstream>
#include <sstream>
#include <rapidjson/document.h>
#include <rapidjson/writer.h>
#include <rapidjson/stringbuffer.h>
#include <rapidjson/prettywriter.h>

namespace vectordb {

int ScalarIndexMeta::init_from_json(const JsonValue& json) {
  if (!json.IsArray()) {
    return -1;
  }
  for (auto& item : json.GetArray()) {
    if (!item.HasMember("FieldName") || !item["FieldName"].IsString()) {
      return -1;
    }
    if (!item.HasMember("FieldType") || !item["FieldType"].IsString()) {
      return -1;
    }
    ScalarIndexItem index_item;
    std::string field_name = item["FieldName"].GetString();
    index_item.field_type = item["FieldType"].GetString();
    items[field_name] = index_item;
  }
  return 0;
}
int ScalarIndexMeta::init_from_file(const std::string& file_path) {
  std::ifstream input(file_path);
  if (!input.is_open()) {
    return -1;
  }
  std::string input_string((std::istreambuf_iterator<char>(input)),
                           std::istreambuf_iterator<char>());

  rapidjson::Document doc;
  doc.Parse(input_string.c_str());
  if (doc.HasParseError()) {
    return -1;
  }
  return init_from_json(doc);
}
int ScalarIndexMeta::save_to_file(const std::string& file_path) {
  JsonStringBuffer buffer;
  JsonPrettyWriter writer(buffer);
  save_to_json(writer);
  std::ofstream ofs(file_path);
  if (!ofs.is_open()) {
    return -1;
  }
  ofs << buffer.GetString();
  ofs.close();
  return 0;
}

int ScalarIndexMeta::save_to_json(JsonPrettyWriter& writer) {
  writer.StartArray();
  for (const auto& iter : items) {
    const auto& field_name = iter.first;
    const auto& item = iter.second;
    writer.StartObject();
    writer.Key("FieldName");
    writer.String(field_name.c_str());
    writer.Key("FieldType");
    writer.String(item.field_type.c_str());
    writer.EndObject();
  }
  writer.EndArray();
  return 0;
}

}  // namespace vectordb
