// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <memory>
#include <fstream>
#include <filesystem>

#include "spdlog/spdlog.h"
#include "common/json_utils.h"

#include "index/detail/meta/scalar_index_meta.h"
#include "index/detail/meta/bruteforce_meta.h"

namespace vectordb {
class ManagerMeta {
 public:
  std::string collection_name;
  std::string index_name;
  uint64_t update_timestamp = 0;
  std::string vector_index_type;

  ScalarIndexMetaPtr scalar_index_meta;
  VectorIndexMetaPtr vector_index_meta;

  int save_to_file(const std::filesystem::path& file_path) {
    JsonStringBuffer buffer;
    JsonPrettyWriter writer(buffer);
    writer.StartObject();
    writer.Key("CollectionName");
    writer.String(collection_name.c_str());
    writer.Key("IndexName");
    writer.String(index_name.c_str());
    writer.Key("UpdateTimeStamp");
    writer.Uint64(update_timestamp);
    if (scalar_index_meta) {
      writer.Key("ScalarIndex");
      scalar_index_meta->save_to_json(writer);
    }
    if (vector_index_meta) {
      writer.Key("VectorIndex");
      writer.StartObject();
      vector_index_meta->save_to_json(writer);
      writer.EndObject();
    }

    writer.EndObject();
    std::ofstream output_file(file_path);
    if (!output_file.is_open()) {
      SPDLOG_ERROR("ManagerMeta::save_to_file failed to open file: {}",
                   file_path.string());
      return -1;
    }
    output_file << buffer.GetString();
    output_file.close();
    return 0;
  }

  int init_from_file(const std::filesystem::path& file_path) {
    JsonDoc doc;
    std::ifstream input_file(file_path);
    if (!input_file.is_open()) {
      SPDLOG_ERROR("ManagerMeta::init_from_file failed to open file: {}",
                   file_path.string());
      return -1;
    }
    std::string content((std::istreambuf_iterator<char>(input_file)),
                        std::istreambuf_iterator<char>());
    input_file.close();
    doc.Parse(content.c_str());
    if (doc.HasParseError()) {
      SPDLOG_ERROR("ManagerMeta::init_from_file ParseError={}",
                   static_cast<int>(doc.GetParseError()));
      return -1;
    }
    return init_from_json(doc);
  }

  int init_from_json(const JsonValue& json) {
    if (json.HasMember("CollectionName")) {
      collection_name = json["CollectionName"].GetString();
    }
    if (json.HasMember("IndexName")) {
      index_name = json["IndexName"].GetString();
    }
    if (json.HasMember("UpdateTimeStamp")) {
      update_timestamp = json["UpdateTimeStamp"].GetUint64();
    }
    if (json.HasMember("VectorIndex")) {
      const auto& vector_index = json["VectorIndex"];
      if (!vector_index.HasMember("IndexType")) {
        SPDLOG_ERROR("ManagerMeta::init_from_json no IndexType");
        return -1;
      } else {
        vector_index_type = vector_index["IndexType"].GetString();

        if (vector_index_type == "flat") {
          vector_index_meta = std::make_shared<BruteForceMeta>();
          if (vector_index_meta->init_from_json(vector_index)) {
            SPDLOG_ERROR(
                "ManagerMeta::init_from_json bf_meta_ init_from_json failed");
            return -1;
          }
        } else {
          SPDLOG_ERROR("ManagerMeta::init_from_json not support index_type={}",
                       vector_index_type.c_str());
          return -1;
        }
      }
    } else {
      SPDLOG_ERROR("ManagerMeta::init_from_json no vector_index");
      return -1;
    }

    if (json.HasMember("ScalarIndex")) {
      const auto& scalar_index = json["ScalarIndex"];
      scalar_index_meta = std::make_shared<ScalarIndexMeta>();
      if (scalar_index_meta->init_from_json(scalar_index)) {
        SPDLOG_ERROR(
            "ManagerMeta::init_from_json scalar_index_meta init_from_json failed");
        return -1;
      }
    }
    return 0;
  }
};

}  // namespace vectordb