#include "bytes_row.h"
#include <cstring>
#include <stdexcept>
#include <algorithm>

namespace vectordb {

// Type size constants
constexpr int INT64_SIZE = 8;
constexpr int UINT64_SIZE = 8;
constexpr int FLOAT32_SIZE = 4;
constexpr int UINT32_SIZE = 4;
constexpr int UINT16_SIZE = 2;
constexpr int BOOL_SIZE = 1;

Schema::Schema(const std::vector<FieldDef>& fields) {
  int current_offset = 1;  // Start after 1 byte header

  if (fields.empty()) {
    total_byte_length_ = current_offset;
    return;
  }

  int max_id = -1;
  for (const auto& field : fields) {
    if (field.id < 0) {
      throw std::invalid_argument("Field id must be non-negative");
    }
    if (field.id > max_id)
      max_id = field.id;
  }

  if (max_id != static_cast<int>(fields.size()) - 1) {
    throw std::invalid_argument(
        "Field ids must be contiguous from 0 to N-1");
  }

  std::vector<bool> seen(fields.size(), false);
  for (const auto& field : fields) {
    if (seen[field.id]) {
      throw std::invalid_argument("Duplicate field id found");
    }
    seen[field.id] = true;
  }

  field_orders_.resize(fields.size());

  for (const auto& field : fields) {
    int byte_len = 0;

    // Calculate basic type size
    switch (field.data_type) {
      case FieldType::INT64:
        byte_len = INT64_SIZE;
        break;
      case FieldType::UINT64:
        byte_len = UINT64_SIZE;
        break;
      case FieldType::FLOAT32:
        byte_len = FLOAT32_SIZE;
        break;
      case FieldType::BOOLEAN:
        byte_len = BOOL_SIZE;
        break;
      case FieldType::STRING:
      case FieldType::BINARY:
      case FieldType::LIST_INT64:
      case FieldType::LIST_STRING:
      case FieldType::LIST_FLOAT32:
        byte_len = UINT32_SIZE;  // Offset
        break;
    }

    FieldMeta meta;
    meta.name = field.name;
    meta.data_type = field.data_type;
    meta.offset = current_offset;
    meta.id = field.id;
    meta.default_value = field.default_value;

    field_metas_[field.name] = meta;
    field_orders_[field.id] = meta;

    current_offset += byte_len;
  }

  total_byte_length_ = current_offset;
}

const FieldMeta* Schema::get_field_meta(const std::string& name) const {
  auto it = field_metas_.find(name);
  if (it == field_metas_.end()) {
    return nullptr;
  }
  return &it->second;
}

BytesRow::BytesRow(std::shared_ptr<Schema> schema) : schema_(schema) {
}

std::string BytesRow::serialize(const std::vector<Value>& row_data) const {
  const auto& field_order = schema_->get_field_order();

  // Pass 1: Calculate total size
  int total_size = schema_->get_total_byte_length();
  int variable_region_offset = total_size;

  // We'll store intermediate results to avoid recalculating
  struct VarFieldInfo {
    int offset;  // Where the data starts in the buffer
    int length;  // Length of the data
  };
  std::vector<VarFieldInfo> var_infos(field_order.size());

  for (size_t i = 0; i < field_order.size(); ++i) {
    const auto& meta = field_order[i];
    const Value& val = (i < row_data.size() &&
                        !std::holds_alternative<std::monostate>(row_data[i]))
                           ? row_data[i]
                           : meta.default_value;

    // Skip fixed size fields calculation (already in total_byte_length_)
    // Only calculate variable length parts
    switch (meta.data_type) {
      case FieldType::STRING: {
        if (std::holds_alternative<std::string>(val)) {
          int len = std::get<std::string>(val).length();
          var_infos[i] = {variable_region_offset, len};
          variable_region_offset += UINT16_SIZE + len;
        } else {
          var_infos[i] = {variable_region_offset, 0};
          variable_region_offset += UINT16_SIZE;
        }
        break;
      }
      case FieldType::BINARY: {
        if (std::holds_alternative<std::string>(
                val)) {  // Binary stored as string
          int len = std::get<std::string>(val).length();
          var_infos[i] = {variable_region_offset, len};
          variable_region_offset += UINT32_SIZE + len;
        } else {
          var_infos[i] = {variable_region_offset, 0};
          variable_region_offset += UINT32_SIZE;
        }
        break;
      }
      case FieldType::LIST_INT64: {
        if (std::holds_alternative<std::vector<int64_t>>(val)) {
          const auto& vec = std::get<std::vector<int64_t>>(val);
          int len = vec.size();
          var_infos[i] = {variable_region_offset, len};
          variable_region_offset += UINT16_SIZE + len * INT64_SIZE;
        } else {
          var_infos[i] = {variable_region_offset, 0};
          variable_region_offset += UINT16_SIZE;
        }
        break;
      }
      case FieldType::LIST_FLOAT32: {
        if (std::holds_alternative<std::vector<float>>(val)) {
          const auto& vec = std::get<std::vector<float>>(val);
          int len = vec.size();
          var_infos[i] = {variable_region_offset, len};
          variable_region_offset += UINT16_SIZE + len * FLOAT32_SIZE;
        } else {
          var_infos[i] = {variable_region_offset, 0};
          variable_region_offset += UINT16_SIZE;
        }
        break;
      }
      case FieldType::LIST_STRING: {
        if (std::holds_alternative<std::vector<std::string>>(val)) {
          const auto& vec = std::get<std::vector<std::string>>(val);
          int len = vec.size();
          var_infos[i] = {variable_region_offset, len};
          variable_region_offset += UINT16_SIZE;  // List length
          for (const auto& s : vec) {
            variable_region_offset += UINT16_SIZE + s.length();
          }
        } else {
          var_infos[i] = {variable_region_offset, 0};
          variable_region_offset += UINT16_SIZE;
        }
        break;
      }
      default:
        break;
    }
  }

  // Allocate buffer
  std::string buffer;
  buffer.resize(variable_region_offset);
  char* ptr = &buffer[0];

  // Write header (field count)
  // Be careful with alignment if we were doing raw casting, but we use memcpy
  // so it's fine.

  uint8_t field_count = static_cast<uint8_t>(field_order.size());
  ptr[0] = field_count;

  // Pass 2: Write data
  for (size_t i = 0; i < field_order.size(); ++i) {
    const auto& meta = field_order[i];
    const Value& val = (i < row_data.size() &&
                        !std::holds_alternative<std::monostate>(row_data[i]))
                           ? row_data[i]
                           : meta.default_value;

    char* field_ptr = ptr + meta.offset;

    switch (meta.data_type) {
      case FieldType::INT64: {
        int64_t v = 0;
        if (std::holds_alternative<int64_t>(val)) {
          v = std::get<int64_t>(val);
        } else if (std::holds_alternative<uint64_t>(val)) {
          // Implicit cast if needed
          v = static_cast<int64_t>(std::get<uint64_t>(val));
        }
        std::memcpy(field_ptr, &v, sizeof(v));
        break;
      }
      case FieldType::UINT64: {
        uint64_t v = 0;
        if (std::holds_alternative<uint64_t>(val)) {
          v = std::get<uint64_t>(val);
        } else if (std::holds_alternative<int64_t>(val)) {
          v = static_cast<uint64_t>(std::get<int64_t>(val));
        }
        std::memcpy(field_ptr, &v, sizeof(v));
        break;
      }
      case FieldType::FLOAT32: {
        float v =
            std::holds_alternative<float>(val) ? std::get<float>(val) : 0.0f;
        std::memcpy(field_ptr, &v, sizeof(v));
        break;
      }
      case FieldType::BOOLEAN: {
        bool v =
            std::holds_alternative<bool>(val) ? std::get<bool>(val) : false;
        uint8_t b = v ? 1 : 0;
        std::memcpy(field_ptr, &b, sizeof(b));
        break;
      }
      // Variable length fields: write offset to fixed region, then write data
      // to variable region
      case FieldType::STRING:
      case FieldType::BINARY:
      case FieldType::LIST_INT64:
      case FieldType::LIST_FLOAT32:
      case FieldType::LIST_STRING: {
        uint32_t offset = static_cast<uint32_t>(var_infos[i].offset);
        std::memcpy(field_ptr, &offset, sizeof(offset));

        char* var_ptr = ptr + offset;

        if (meta.data_type == FieldType::STRING) {
          const std::string& s = std::holds_alternative<std::string>(val)
                                     ? std::get<std::string>(val)
                                     : "";
          uint16_t len = static_cast<uint16_t>(s.length());
          std::memcpy(var_ptr, &len, sizeof(len));
          if (len > 0)
            std::memcpy(var_ptr + sizeof(len), s.data(), len);
        } else if (meta.data_type == FieldType::BINARY) {
          const std::string& s = std::holds_alternative<std::string>(val)
                                     ? std::get<std::string>(val)
                                     : "";
          uint32_t len = static_cast<uint32_t>(s.length());
          std::memcpy(var_ptr, &len, sizeof(len));
          if (len > 0)
            std::memcpy(var_ptr + sizeof(len), s.data(), len);
        } else if (meta.data_type == FieldType::LIST_INT64) {
          const auto& vec = std::holds_alternative<std::vector<int64_t>>(val)
                                ? std::get<std::vector<int64_t>>(val)
                                : std::vector<int64_t>{};
          uint16_t len = static_cast<uint16_t>(vec.size());
          std::memcpy(var_ptr, &len, sizeof(len));
          if (len > 0)
            std::memcpy(var_ptr + sizeof(len), vec.data(),
                        len * sizeof(int64_t));
        } else if (meta.data_type == FieldType::LIST_FLOAT32) {
          const auto& vec = std::holds_alternative<std::vector<float>>(val)
                                ? std::get<std::vector<float>>(val)
                                : std::vector<float>{};
          uint16_t len = static_cast<uint16_t>(vec.size());
          std::memcpy(var_ptr, &len, sizeof(len));
          if (len > 0)
            std::memcpy(var_ptr + sizeof(len), vec.data(), len * sizeof(float));
        } else if (meta.data_type == FieldType::LIST_STRING) {
          const auto& vec =
              std::holds_alternative<std::vector<std::string>>(val)
                  ? std::get<std::vector<std::string>>(val)
                  : std::vector<std::string>{};
          uint16_t len = static_cast<uint16_t>(vec.size());
          std::memcpy(var_ptr, &len, sizeof(len));
          var_ptr += sizeof(len);
          for (const auto& s : vec) {
            uint16_t s_len = static_cast<uint16_t>(s.length());
            std::memcpy(var_ptr, &s_len, sizeof(s_len));
            var_ptr += sizeof(s_len);
            if (s_len > 0)
              std::memcpy(var_ptr, s.data(), s_len);
            var_ptr += s_len;
          }
        }
        break;
      }
    }
  }

  return buffer;
}

Value BytesRow::deserialize_field(const std::string& serialized_data,
                                  const std::string& field_name) const {
  const FieldMeta* meta_ptr = schema_->get_field_meta(field_name);
  if (!meta_ptr)
    return std::monostate{};

  const FieldMeta& meta = *meta_ptr;
  const char* ptr = serialized_data.data();

  // Check if data is large enough for this field's offset
  if (serialized_data.size() <= static_cast<size_t>(meta.offset)) {
    return meta.default_value;
  }

  uint8_t field_count = static_cast<uint8_t>(ptr[0]);
  if (meta.id >= field_count) {
    return meta.default_value;
  }

  const char* field_ptr = ptr + meta.offset;

  switch (meta.data_type) {
    case FieldType::INT64: {
      int64_t v;
      std::memcpy(&v, field_ptr, sizeof(v));
      return v;
    }
    case FieldType::UINT64: {
      uint64_t v;
      std::memcpy(&v, field_ptr, sizeof(v));
      return v;
    }
    case FieldType::FLOAT32: {
      float v;
      std::memcpy(&v, field_ptr, sizeof(v));
      return v;
    }
    case FieldType::BOOLEAN: {
      uint8_t b;
      std::memcpy(&b, field_ptr, sizeof(b));
      return (bool)b;
    }
    case FieldType::STRING: {
      uint32_t offset;
      if (sizeof(offset) >
          serialized_data.size() -
              static_cast<size_t>(field_ptr - serialized_data.data()))
        return std::string("");
      std::memcpy(&offset, field_ptr, sizeof(offset));
      if (offset >= serialized_data.size())
        return std::string("");

      uint16_t len;
      if (offset + sizeof(len) > serialized_data.size())
        return std::string("");
      std::memcpy(&len, ptr + offset, sizeof(len));

      if (static_cast<size_t>(offset) + sizeof(len) + len >
          serialized_data.size())
        return std::string("");
      return std::string(ptr + offset + sizeof(len), len);
    }
    case FieldType::BINARY: {
      uint32_t offset;
      if (sizeof(offset) >
          serialized_data.size() -
              static_cast<size_t>(field_ptr - serialized_data.data()))
        return std::string("");
      std::memcpy(&offset, field_ptr, sizeof(offset));
      if (offset >= serialized_data.size())
        return std::string("");

      uint32_t len;
      if (offset + sizeof(len) > serialized_data.size())
        return std::string("");
      std::memcpy(&len, ptr + offset, sizeof(len));

      if (static_cast<size_t>(offset) + sizeof(len) + len >
          serialized_data.size())
        return std::string("");
      return std::string(ptr + offset + sizeof(len), len);
    }
    case FieldType::LIST_INT64: {
      uint32_t offset;
      if (sizeof(offset) >
          serialized_data.size() -
              static_cast<size_t>(field_ptr - serialized_data.data()))
        return std::vector<int64_t>{};
      std::memcpy(&offset, field_ptr, sizeof(offset));
      if (offset >= serialized_data.size())
        return std::vector<int64_t>{};

      uint16_t len;
      if (offset + sizeof(len) > serialized_data.size())
        return std::vector<int64_t>{};
      std::memcpy(&len, ptr + offset, sizeof(len));

      std::vector<int64_t> vec(len);
      if (len > 0) {
        if (static_cast<size_t>(offset) + sizeof(len) + len * sizeof(int64_t) >
            serialized_data.size())
          return std::vector<int64_t>{};
        std::memcpy(vec.data(), ptr + offset + sizeof(len),
                    len * sizeof(int64_t));
      }
      return vec;
    }
    case FieldType::LIST_FLOAT32: {
      uint32_t offset;
      if (sizeof(offset) >
          serialized_data.size() -
              static_cast<size_t>(field_ptr - serialized_data.data()))
        return std::vector<float>{};
      std::memcpy(&offset, field_ptr, sizeof(offset));
      if (offset >= serialized_data.size())
        return std::vector<float>{};

      uint16_t len;
      if (offset + sizeof(len) > serialized_data.size())
        return std::vector<float>{};
      std::memcpy(&len, ptr + offset, sizeof(len));

      std::vector<float> vec(len);
      if (len > 0) {
        if (static_cast<size_t>(offset) + sizeof(len) + len * sizeof(float) >
            serialized_data.size())
          return std::vector<float>{};
        std::memcpy(vec.data(), ptr + offset + sizeof(len),
                    len * sizeof(float));
      }
      return vec;
    }
    case FieldType::LIST_STRING: {
      uint32_t offset;
      if (sizeof(offset) >
          serialized_data.size() -
              static_cast<size_t>(field_ptr - serialized_data.data()))
        return std::vector<std::string>{};
      std::memcpy(&offset, field_ptr, sizeof(offset));
      if (offset >= serialized_data.size())
        return std::vector<std::string>{};

      const char* var_ptr = ptr + offset;
      uint16_t list_len;

      if (static_cast<size_t>(offset) + sizeof(list_len) >
          serialized_data.size())
        return std::vector<std::string>{};
      std::memcpy(&list_len, var_ptr, sizeof(list_len));
      var_ptr += sizeof(list_len);

      std::vector<std::string> vec;
      vec.reserve(list_len);
      for (int i = 0; i < list_len; ++i) {
        uint16_t s_len;
        if (static_cast<size_t>(var_ptr - ptr) + sizeof(s_len) >
            serialized_data.size())
          break;
        std::memcpy(&s_len, var_ptr, sizeof(s_len));
        var_ptr += sizeof(s_len);

        if (static_cast<size_t>(var_ptr - ptr) + s_len > serialized_data.size())
          break;
        vec.emplace_back(var_ptr, s_len);
        var_ptr += s_len;
      }
      return vec;
    }
  }
  return std::monostate{};
}

std::map<std::string, Value> BytesRow::deserialize(
    const std::string& serialized_data) const {
  std::map<std::string, Value> result;
  const auto& order = schema_->get_field_order();
  for (const auto& meta : order) {
    result[meta.name] = deserialize_field(serialized_data, meta.name);
  }
  return result;
}

}  // namespace vectordb
