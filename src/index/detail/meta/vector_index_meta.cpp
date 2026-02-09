// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#include "index/detail/meta/vector_index_meta.h"
#include "spdlog/spdlog.h"
#include <rapidjson/writer.h>
#include <rapidjson/stringbuffer.h>
#include <rapidjson/prettywriter.h>
#include <fstream>
#include <sstream>

namespace vectordb {

int VectorIndexMeta::init_from_json(const JsonValue& json) {
  if (!json.HasMember("IndexType")) {
    SPDLOG_ERROR("VectorIndexMeta::init_from_json no IndexType");
    return -1;
  }
  index_type = json["IndexType"].GetString();
  if (!json.HasMember("ElementCount")) {
    SPDLOG_ERROR("VectorIndexMeta::init_from_json no ElementCount");
    return -1;
  }
  element_count = json["ElementCount"].GetUint64();
  if (!json.HasMember("MaxElementCount")) {
    SPDLOG_ERROR("VectorIndexMeta::init_from_json no max_element_count");
    return -1;
  }
  max_element_count = json["MaxElementCount"].GetUint64();
  if (!json.HasMember("Dimension")) {
    SPDLOG_ERROR("VectorIndexMeta::init_from_json no Dimension");
    return -1;
  }
  dimension = json["Dimension"].GetUint64();
  if (dimension == 0) {
    SPDLOG_ERROR("VectorIndexMeta::init_from_json invalid dimension: 0");
    return -1;
  }
  if (json.HasMember("Distance")) {
    distance_type = json["Distance"].GetString();
  }
  if (json.HasMember("Quant")) {
    quantization_type = json["Quant"].GetString();
  }
  if (json.HasMember("EnableSparse")) {
    enable_sparse = json["EnableSparse"].GetBool();
  }
  if (json.HasMember("SearchWithSparseLogitAlpha")) {
    search_with_sparse_logit_alpha =
        json["SearchWithSparseLogitAlpha"].GetFloat();
  }
  return 0;
}

int VectorIndexMeta::save_to_file(const std::string& file_path) {
  rapidjson::StringBuffer buffer;
  rapidjson::PrettyWriter<rapidjson::StringBuffer> writer(buffer);
  writer.StartObject();
  save_to_json(writer);
  writer.EndObject();
  std::ofstream output_file(file_path);
  if (!output_file.is_open()) {
    return -1;
  }
  output_file << buffer.GetString();
  output_file.close();
  return 0;
}

int VectorIndexMeta::init_from_file(const std::string& file_path) {
  std::ifstream input_file(file_path);
  if (!input_file.is_open()) {
    return -1;
  }
  std::string input_string((std::istreambuf_iterator<char>(input_file)),
                           std::istreambuf_iterator<char>());
  rapidjson::Document document;
  document.Parse(input_string.c_str());
  if (document.HasParseError()) {
    return -1;
  }
  return init_from_json(document);
}

int VectorIndexMeta::save_to_json(JsonPrettyWriter& writer) {
  writer.Key("IndexType");
  writer.String(index_type.c_str());
  writer.Key("ElementCount");
  writer.Uint64(element_count);
  writer.Key("MaxElementCount");
  writer.Uint64(max_element_count);
  writer.Key("Dimension");
  writer.Uint64(dimension);
  writer.Key("Distance");
  writer.String(distance_type.c_str());
  writer.Key("Quant");
  writer.String(quantization_type.c_str());
  writer.Key("EnableSparse");
  writer.Bool(enable_sparse);
  writer.Key("SearchWithSparseLogitAlpha");
  writer.Double(search_with_sparse_logit_alpha);
  return 0;
}

}  // namespace vectordb
