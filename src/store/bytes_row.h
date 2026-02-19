#pragma once

#include <string>
#include <vector>
#include <map>
#include <variant>
#include <memory>
#include <cstdint>
#include <cstring>

namespace vectordb {

enum class FieldType {
    INT64 = 0,
    UINT64 = 1,
    FLOAT32 = 2,
    STRING = 3,
    BINARY = 4,
    BOOLEAN = 5,
    LIST_INT64 = 6,
    LIST_STRING = 7,
    LIST_FLOAT32 = 8
};

// Use std::monostate for None/Null
using Value = std::variant<std::monostate, int64_t, uint64_t, float, std::string, bool, std::vector<int64_t>, std::vector<std::string>, std::vector<float>>;

struct FieldMeta {
    std::string name;
    FieldType data_type;
    int offset;
    int id;
    Value default_value;
};

struct FieldDef {
    std::string name;
    FieldType data_type;
    int id;
    Value default_value;
};

class Schema {
public:
    Schema(const std::vector<FieldDef>& fields);
    
    const std::vector<FieldMeta>& get_field_order() const { return field_orders_; }
    int get_total_byte_length() const { return total_byte_length_; }
    const FieldMeta* get_field_meta(const std::string& name) const;

private:
    std::vector<FieldMeta> field_orders_;
    std::map<std::string, FieldMeta> field_metas_;
    int total_byte_length_;
};

class BytesRow {
public:
    explicit BytesRow(std::shared_ptr<Schema> schema);
    
    // Core serialization logic: takes values in order of schema fields
    std::string serialize(const std::vector<Value>& row_data) const;
    
    // Generic serialization template
    // Accessor must implement methods like:
    // int64_t get_int64(const RowT& row, int field_idx)
    // ... and so on for all types, plus is_null/has_value check
    template <typename RowT, typename AccessorT>
    std::string serialize_template(const RowT& row, const AccessorT& accessor) const {
        const auto& field_order = schema_->get_field_order();

        auto get_default_string = [](const Value& val) -> const std::string* {
            if (std::holds_alternative<std::string>(val)) {
                return &std::get<std::string>(val);
            }
            return nullptr;
        };

        auto get_default_list_int64 = [](const Value& val) -> const std::vector<int64_t>* {
            if (std::holds_alternative<std::vector<int64_t>>(val)) {
                return &std::get<std::vector<int64_t>>(val);
            }
            return nullptr;
        };

        auto get_default_list_float32 = [](const Value& val) -> const std::vector<float>* {
            if (std::holds_alternative<std::vector<float>>(val)) {
                return &std::get<std::vector<float>>(val);
            }
            return nullptr;
        };

        auto get_default_list_string = [](const Value& val) -> const std::vector<std::string>* {
            if (std::holds_alternative<std::vector<std::string>>(val)) {
                return &std::get<std::vector<std::string>>(val);
            }
            return nullptr;
        };

        auto get_list_string_content_len = [](const std::vector<std::string>& vec) -> int {
            int total = 0;
            for (const auto& s : vec) {
                total += static_cast<int>(s.length());
            }
            return total;
        };

        auto write_string_value = [](const std::string& s, char* dest) {
            uint16_t len = static_cast<uint16_t>(s.length());
            std::memcpy(dest, &len, 2);
            if (len > 0) {
                std::memcpy(dest + 2, s.data(), len);
            }
        };

        auto write_binary_value = [](const std::string& s, char* dest) {
            uint32_t len = static_cast<uint32_t>(s.length());
            std::memcpy(dest, &len, 4);
            if (len > 0) {
                std::memcpy(dest + 4, s.data(), len);
            }
        };

        auto write_list_int64_value = [](const std::vector<int64_t>& vec, char* dest) {
            uint16_t len = static_cast<uint16_t>(vec.size());
            std::memcpy(dest, &len, 2);
            if (len > 0) {
                std::memcpy(dest + 2, vec.data(), len * sizeof(int64_t));
            }
        };

        auto write_list_float32_value = [](const std::vector<float>& vec, char* dest) {
            uint16_t len = static_cast<uint16_t>(vec.size());
            std::memcpy(dest, &len, 2);
            if (len > 0) {
                std::memcpy(dest + 2, vec.data(), len * sizeof(float));
            }
        };

        auto write_list_string_value = [](const std::vector<std::string>& vec, char* dest) {
            uint16_t len = static_cast<uint16_t>(vec.size());
            std::memcpy(dest, &len, 2);
            char* cur = dest + 2;
            for (const auto& s : vec) {
                uint16_t slen = static_cast<uint16_t>(s.length());
                std::memcpy(cur, &slen, 2);
                cur += 2;
                if (slen > 0) {
                    std::memcpy(cur, s.data(), slen);
                }
                cur += slen;
            }
        };
        
        // Pass 1: Calculate total size
        int total_size = schema_->get_total_byte_length();
        int variable_region_offset = total_size;
        
        struct VarFieldInfo {
            int offset; 
            int length; 
        };
        // Use a small buffer on stack if possible, or vector
        std::vector<VarFieldInfo> var_infos(field_order.size());
        
        for (size_t i = 0; i < field_order.size(); ++i) {
            const auto& meta = field_order[i];
            
            // For variable fields, we need to check length
            switch (meta.data_type) {
                case FieldType::STRING: {
                    int len = 0;
                    if (accessor.has_value(row, i)) {
                        len = accessor.get_string_len(row, i);
                    } else if (const auto* def = get_default_string(meta.default_value)) {
                        len = static_cast<int>(def->length());
                    }
                    var_infos[i] = {variable_region_offset, len};
                    variable_region_offset += 2 + len; // UINT16_SIZE
                    break;
                }
                case FieldType::BINARY: {
                    int len = 0;
                    if (accessor.has_value(row, i)) {
                        len = accessor.get_binary_len(row, i);
                    } else if (const auto* def = get_default_string(meta.default_value)) {
                        len = static_cast<int>(def->length());
                    }
                    var_infos[i] = {variable_region_offset, len};
                    variable_region_offset += 4 + len; // UINT32_SIZE
                    break;
                }
                case FieldType::LIST_INT64: {
                    int len = 0;
                    if (accessor.has_value(row, i)) {
                        len = accessor.get_list_len(row, i);
                    } else if (const auto* def = get_default_list_int64(meta.default_value)) {
                        len = static_cast<int>(def->size());
                    }
                    var_infos[i] = {variable_region_offset, len};
                    variable_region_offset += 2 + len * 8; // UINT16 + INT64_SIZE
                    break;
                }
                case FieldType::LIST_FLOAT32: {
                    int len = 0;
                    if (accessor.has_value(row, i)) {
                        len = accessor.get_list_len(row, i);
                    } else if (const auto* def = get_default_list_float32(meta.default_value)) {
                        len = static_cast<int>(def->size());
                    }
                    var_infos[i] = {variable_region_offset, len};
                    variable_region_offset += 2 + len * 4; // UINT16 + FLOAT32_SIZE
                    break;
                }
                case FieldType::LIST_STRING: {
                    int list_len = 0;
                    int content_len = 0;
                    if (accessor.has_value(row, i)) {
                        list_len = accessor.get_list_len(row, i);
                        content_len = accessor.get_list_string_content_len(row, i);
                    } else if (const auto* def = get_default_list_string(meta.default_value)) {
                        list_len = static_cast<int>(def->size());
                        content_len = get_list_string_content_len(*def);
                    }
                    var_infos[i] = {variable_region_offset, list_len};
                    // list_len(2) + (elem_len(2) + content) * N
                    // Actually content_len should include the 2 bytes for each string length if we compute it that way
                    // Or we compute it here: 2 + (2 * list_len) + total_string_bytes
                    variable_region_offset += 2 + (2 * list_len) + content_len; 
                    break;
                }
                default: break;
            }
        }
        
        std::string buffer;
        buffer.resize(variable_region_offset);
        char* ptr = &buffer[0];
        
        // Header
        ptr[0] = static_cast<uint8_t>(field_order.size());
        
        // Pass 2: Write data
        for (size_t i = 0; i < field_order.size(); ++i) {
            const auto& meta = field_order[i];
            char* field_ptr = ptr + meta.offset;
            bool has_val = accessor.has_value(row, i);
            
            switch (meta.data_type) {
                case FieldType::INT64: {
                    int64_t v = 0;
                    if (has_val) {
                        v = accessor.get_int64(row, i);
                    } else if (std::holds_alternative<int64_t>(meta.default_value)) {
                        v = std::get<int64_t>(meta.default_value);
                    } else if (std::holds_alternative<uint64_t>(meta.default_value)) {
                        v = static_cast<int64_t>(std::get<uint64_t>(meta.default_value));
                    }
                    std::memcpy(field_ptr, &v, sizeof(v));
                    break;
                }
                case FieldType::UINT64: {
                    uint64_t v = 0;
                    if (has_val) {
                        v = accessor.get_uint64(row, i);
                    } else if (std::holds_alternative<uint64_t>(meta.default_value)) {
                        v = std::get<uint64_t>(meta.default_value);
                    } else if (std::holds_alternative<int64_t>(meta.default_value)) {
                        v = static_cast<uint64_t>(std::get<int64_t>(meta.default_value));
                    }
                    std::memcpy(field_ptr, &v, sizeof(v));
                    break;
                }
                case FieldType::FLOAT32: {
                    float v = 0.0f;
                    if (has_val) {
                        v = accessor.get_float(row, i);
                    } else if (std::holds_alternative<float>(meta.default_value)) {
                        v = std::get<float>(meta.default_value);
                    }
                    std::memcpy(field_ptr, &v, sizeof(v));
                    break;
                }
                case FieldType::BOOLEAN: {
                    bool v = false;
                    if (has_val) {
                        v = accessor.get_bool(row, i);
                    } else if (std::holds_alternative<bool>(meta.default_value)) {
                        v = std::get<bool>(meta.default_value);
                    }
                    uint8_t b = v ? 1 : 0;
                    std::memcpy(field_ptr, &b, sizeof(b));
                    break;
                }
                case FieldType::STRING:
                case FieldType::BINARY:
                case FieldType::LIST_INT64:
                case FieldType::LIST_FLOAT32:
                case FieldType::LIST_STRING: {
                    uint32_t offset = static_cast<uint32_t>(var_infos[i].offset);
                    std::memcpy(field_ptr, &offset, sizeof(offset));
                    
                    char* var_ptr = ptr + offset;
                    
                    if (meta.data_type == FieldType::STRING) {
                         // Logic handled by accessor to write directly? Or return string_view/string?
                         // Accessor returning string creates copy. 
                         // Better: accessor.write_string(row, i, var_ptr)
                         if (has_val) {
                             accessor.write_string(row, i, var_ptr);
                         } else if (const auto* def = get_default_string(meta.default_value)) {
                             write_string_value(*def, var_ptr);
                         } else {
                             uint16_t len = 0;
                             std::memcpy(var_ptr, &len, 2);
                         }
                    } else if (meta.data_type == FieldType::BINARY) {
                         if (has_val) {
                             accessor.write_binary(row, i, var_ptr);
                         } else if (const auto* def = get_default_string(meta.default_value)) {
                             write_binary_value(*def, var_ptr);
                         } else {
                             uint32_t len = 0;
                             std::memcpy(var_ptr, &len, 4);
                         }
                    } else if (meta.data_type == FieldType::LIST_INT64) {
                         if (has_val) {
                             accessor.write_list_int64(row, i, var_ptr);
                         } else if (const auto* def = get_default_list_int64(meta.default_value)) {
                             write_list_int64_value(*def, var_ptr);
                         } else {
                             uint16_t len = 0;
                             std::memcpy(var_ptr, &len, 2);
                         }
                    } else if (meta.data_type == FieldType::LIST_FLOAT32) {
                         if (has_val) {
                             accessor.write_list_float32(row, i, var_ptr);
                         } else if (const auto* def = get_default_list_float32(meta.default_value)) {
                             write_list_float32_value(*def, var_ptr);
                         } else {
                             uint16_t len = 0;
                             std::memcpy(var_ptr, &len, 2);
                         }
                    } else if (meta.data_type == FieldType::LIST_STRING) {
                         if (has_val) {
                             accessor.write_list_string(row, i, var_ptr);
                         } else if (const auto* def = get_default_list_string(meta.default_value)) {
                             write_list_string_value(*def, var_ptr);
                         } else {
                             uint16_t len = 0;
                             std::memcpy(var_ptr, &len, 2);
                         }
                    }
                    break;
                }
            }
        }
        return buffer;
    }

    // Deserialize to a map
    std::map<std::string, Value> deserialize(const std::string& serialized_data) const;
    
    // Deserialize a single field
    Value deserialize_field(const std::string& serialized_data, const std::string& field_name) const;

    // Get schema
    const Schema& get_schema() const { return *schema_; }
    
private:
    std::shared_ptr<Schema> schema_;
};

} // namespace vectordb
