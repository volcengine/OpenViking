// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include <map>
#include <vector>
#include <unordered_map>
#include <rapidjson/document.h>
#include <rapidjson/error/en.h>
#include <rapidjson/stringbuffer.h>
#include <rapidjson/prettywriter.h>
#include <rapidjson/writer.h>
#include <limits>
#include <string>
#include <memory>
#include <type_traits>

namespace vectordb {

using JsonDoc = rapidjson::Document;
using JsonDocPtr = std::shared_ptr<JsonDoc>;
using JsonValue = rapidjson::Value;
using JsonMemberIterator = rapidjson::Document::MemberIterator;
using JsonConstMemberIterator = rapidjson::Document::ConstMemberIterator;
using JsonAllocator = JsonDoc::AllocatorType;
using SubtreeIndex = uint32_t;
using TagkvList = std::vector<std::pair<std::string, std::string>>;
using JsonStringBuffer = rapidjson::StringBuffer;
using JsonPrettyWriter = rapidjson::PrettyWriter<rapidjson::StringBuffer>;

template <typename JsonValue_>
std::string json_stringify(const JsonValue_& value) {
  rapidjson::StringBuffer buffer;
  rapidjson::Writer<rapidjson::StringBuffer> writer(buffer);
  value.Accept(writer);
  return buffer.GetString();
}

template <typename JsonValue_>
void json_remove_null_keys(JsonValue_* value) {
  if (value->IsObject()) {
    for (auto it = value->MemberBegin(); it != value->MemberEnd();) {
      json_remove_null_keys(&it->value);
      if (it->value.IsNull()) {
        it = value->EraseMember(it);
      } else {
        ++it;
      }
    }
    if (value->MemberCount() == 0) {
      value->SetNull();
    }
  }
}

inline void merge_json_values_impl(JsonValue* target, JsonValue& source,
                                   JsonAllocator& allocator, bool move_source) {
  if (!target || source.IsNull()) {
    return;
  }

  if (source.IsObject()) {
    if (!target->IsObject()) {
      target->SetObject();
    }

    for (auto it = source.MemberBegin(); it != source.MemberEnd(); ++it) {
      auto target_itr = target->FindMember(it->name);

      if (target_itr != target->MemberEnd()) {
        if (target_itr->value.IsNumber() && it->value.IsNumber()) {
          if (target_itr->value.IsInt() && it->value.IsInt()) {
            target_itr->value.SetInt(target_itr->value.GetInt() +
                                     it->value.GetInt());
          } else if (target_itr->value.IsUint() && it->value.IsUint()) {
            target_itr->value.SetUint(target_itr->value.GetUint() +
                                      it->value.GetUint());
          } else if (target_itr->value.IsInt64() && it->value.IsInt64()) {
            target_itr->value.SetInt64(target_itr->value.GetInt64() +
                                       it->value.GetInt64());
          } else if (target_itr->value.IsUint64() && it->value.IsUint64()) {
            target_itr->value.SetUint64(target_itr->value.GetUint64() +
                                        it->value.GetUint64());
          } else {
            target_itr->value.SetDouble(target_itr->value.GetDouble() +
                                        it->value.GetDouble());
          }
        } else if (target_itr->value.IsObject() && it->value.IsObject()) {
          merge_json_values_impl(&(target_itr->value), it->value, allocator,
                                 move_source);
        } else {
          if (move_source) {
            target_itr->value = std::move(it->value);
          } else {
            target_itr->value.CopyFrom(it->value, allocator);
          }
        }
      } else {
        if (move_source) {
          target->AddMember(std::move(it->name), std::move(it->value),
                            allocator);
        } else {
          JsonValue key_copy;
          key_copy.CopyFrom(it->name, allocator);
          JsonValue val_copy;
          val_copy.CopyFrom(it->value, allocator);
          target->AddMember(key_copy, val_copy, allocator);
        }
      }
    }
  } else {
    if (move_source) {
      *target = std::move(source);
    } else {
      target->CopyFrom(source, allocator);
    }
  }
}

inline void merge_json_values(JsonValue* target, const JsonValue& source,
                              JsonAllocator& allocator) {
  merge_json_values_impl(target, const_cast<JsonValue&>(source), allocator,
                         false);
}

inline void merge_json_values(JsonValue* target, JsonValue&& source,
                              JsonAllocator& allocator) {
  merge_json_values_impl(target, source, allocator, true);
}

}  // namespace vectordb
