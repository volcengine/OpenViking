// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <cstring>
#include "store/bytes_row.h"

namespace py = pybind11;
namespace vdb = vectordb;

// Accessor for Python Dict
class PyDictAccessor {
 public:
  PyDictAccessor(const vdb::Schema& schema)
      : field_order_(schema.get_field_order()) {
  }

  bool has_value(const py::dict& row, int field_idx) const {
    const auto& name = field_order_[field_idx].name;
    if (!row.contains(name.c_str()))
      return false;
    return !row[name.c_str()].is_none();
  }

  int64_t get_int64(const py::dict& row, int field_idx) const {
    return row[field_order_[field_idx].name.c_str()].cast<int64_t>();
  }

  uint64_t get_uint64(const py::dict& row, int field_idx) const {
    return row[field_order_[field_idx].name.c_str()].cast<uint64_t>();
  }

  float get_float(const py::dict& row, int field_idx) const {
    return row[field_order_[field_idx].name.c_str()].cast<float>();
  }

  bool get_bool(const py::dict& row, int field_idx) const {
    return row[field_order_[field_idx].name.c_str()].cast<bool>();
  }

  int get_string_len(const py::dict& row, int field_idx) const {
    py::object val = row[field_order_[field_idx].name.c_str()];
    // Assume it's string or bytes
    if (py::isinstance<py::bytes>(val)) {
      return PyBytes_Size(val.ptr());
    }
    return val.cast<std::string>().length();
  }

  int get_binary_len(const py::dict& row, int field_idx) const {
    // Same as string for length
    return get_string_len(row, field_idx);
  }

  int get_list_len(const py::dict& row, int field_idx) const {
    py::list l = row[field_order_[field_idx].name.c_str()].cast<py::list>();
    return static_cast<int>(l.size());
  }

  int get_list_string_content_len(const py::dict& row, int field_idx) const {
    py::list l = row[field_order_[field_idx].name.c_str()].cast<py::list>();
    int total = 0;
    for (auto item : l) {
      total += item.cast<std::string>().length();
    }
    return total;
  }

  // Writers
  void write_string(const py::dict& row, int field_idx, char* dest) const {
    std::string s =
        row[field_order_[field_idx].name.c_str()].cast<std::string>();
    uint16_t len = static_cast<uint16_t>(s.length());
    std::memcpy(dest, &len, 2);
    if (len > 0)
      std::memcpy(dest + 2, s.data(), len);
  }

  void write_binary(const py::dict& row, int field_idx, char* dest) const {
    std::string s =
        row[field_order_[field_idx].name.c_str()].cast<std::string>();
    uint32_t len = static_cast<uint32_t>(s.length());
    std::memcpy(dest, &len, 4);
    if (len > 0)
      std::memcpy(dest + 4, s.data(), len);
  }

  void write_list_int64(const py::dict& row, int field_idx, char* dest) const {
    py::list l = row[field_order_[field_idx].name.c_str()].cast<py::list>();
    uint16_t len = static_cast<uint16_t>(l.size());
    std::memcpy(dest, &len, 2);
    int64_t* data_ptr = reinterpret_cast<int64_t*>(dest + 2);
    for (size_t i = 0; i < len; ++i) {
      data_ptr[i] = l[i].cast<int64_t>();
    }
  }

  void write_list_float32(const py::dict& row, int field_idx,
                          char* dest) const {
    py::list l = row[field_order_[field_idx].name.c_str()].cast<py::list>();
    uint16_t len = static_cast<uint16_t>(l.size());
    std::memcpy(dest, &len, 2);
    float* data_ptr = reinterpret_cast<float*>(dest + 2);
    for (size_t i = 0; i < len; ++i) {
      data_ptr[i] = l[i].cast<float>();
    }
  }

  void write_list_string(const py::dict& row, int field_idx, char* dest) const {
    py::list l = row[field_order_[field_idx].name.c_str()].cast<py::list>();
    uint16_t len = static_cast<uint16_t>(l.size());
    std::memcpy(dest, &len, 2);
    char* cur = dest + 2;
    for (size_t i = 0; i < len; ++i) {
      std::string s = l[i].cast<std::string>();
      uint16_t slen = static_cast<uint16_t>(s.length());
      std::memcpy(cur, &slen, 2);
      cur += 2;
      if (slen > 0)
        std::memcpy(cur, s.data(), slen);
      cur += slen;
    }
  }

 private:
  const std::vector<vdb::FieldMeta>& field_order_;
};

// Accessor for Python Object
class PyObjectAccessor {
 public:
  PyObjectAccessor(const vdb::Schema& schema)
      : field_order_(schema.get_field_order()) {
  }

  bool has_value(const py::handle& row, int field_idx) const {
    const char* name = field_order_[field_idx].name.c_str();
    if (!py::hasattr(row, name))
      return false;
    return !row.attr(name).is_none();
  }

  int64_t get_int64(const py::handle& row, int field_idx) const {
    return row.attr(field_order_[field_idx].name.c_str()).cast<int64_t>();
  }

  uint64_t get_uint64(const py::handle& row, int field_idx) const {
    return row.attr(field_order_[field_idx].name.c_str()).cast<uint64_t>();
  }

  float get_float(const py::handle& row, int field_idx) const {
    return row.attr(field_order_[field_idx].name.c_str()).cast<float>();
  }

  bool get_bool(const py::handle& row, int field_idx) const {
    return row.attr(field_order_[field_idx].name.c_str()).cast<bool>();
  }

  int get_string_len(const py::handle& row, int field_idx) const {
    // See comments in PyDictAccessor about encoding efficiency
    return row.attr(field_order_[field_idx].name.c_str())
        .cast<std::string>()
        .length();
  }

  int get_binary_len(const py::handle& row, int field_idx) const {
    return get_string_len(row, field_idx);
  }

  int get_list_len(const py::handle& row, int field_idx) const {
    py::list l =
        row.attr(field_order_[field_idx].name.c_str()).cast<py::list>();
    return static_cast<int>(l.size());
  }

  int get_list_string_content_len(const py::handle& row, int field_idx) const {
    py::list l =
        row.attr(field_order_[field_idx].name.c_str()).cast<py::list>();
    int total = 0;
    for (auto item : l) {
      total += item.cast<std::string>().length();
    }
    return total;
  }

  void write_string(const py::handle& row, int field_idx, char* dest) const {
    std::string s =
        row.attr(field_order_[field_idx].name.c_str()).cast<std::string>();
    uint16_t len = static_cast<uint16_t>(s.length());
    std::memcpy(dest, &len, 2);
    if (len > 0)
      std::memcpy(dest + 2, s.data(), len);
  }

  void write_binary(const py::handle& row, int field_idx, char* dest) const {
    std::string s =
        row.attr(field_order_[field_idx].name.c_str()).cast<std::string>();
    uint32_t len = static_cast<uint32_t>(s.length());
    std::memcpy(dest, &len, 4);
    if (len > 0)
      std::memcpy(dest + 4, s.data(), len);
  }

  void write_list_int64(const py::handle& row, int field_idx,
                        char* dest) const {
    py::list l =
        row.attr(field_order_[field_idx].name.c_str()).cast<py::list>();
    uint16_t len = static_cast<uint16_t>(l.size());
    std::memcpy(dest, &len, 2);
    int64_t* data_ptr = reinterpret_cast<int64_t*>(dest + 2);
    for (size_t i = 0; i < len; ++i) {
      data_ptr[i] = l[i].cast<int64_t>();
    }
  }

  void write_list_float32(const py::handle& row, int field_idx,
                          char* dest) const {
    py::list l =
        row.attr(field_order_[field_idx].name.c_str()).cast<py::list>();
    uint16_t len = static_cast<uint16_t>(l.size());
    std::memcpy(dest, &len, 2);
    float* data_ptr = reinterpret_cast<float*>(dest + 2);
    for (size_t i = 0; i < len; ++i) {
      data_ptr[i] = l[i].cast<float>();
    }
  }

  void write_list_string(const py::handle& row, int field_idx,
                         char* dest) const {
    py::list l =
        row.attr(field_order_[field_idx].name.c_str()).cast<py::list>();
    uint16_t len = static_cast<uint16_t>(l.size());
    std::memcpy(dest, &len, 2);
    char* cur = dest + 2;
    for (size_t i = 0; i < len; ++i) {
      std::string s = l[i].cast<std::string>();
      uint16_t slen = static_cast<uint16_t>(s.length());
      std::memcpy(cur, &slen, 2);
      cur += 2;
      if (slen > 0)
        std::memcpy(cur, s.data(), slen);
      cur += slen;
    }
  }

 private:
  const std::vector<vdb::FieldMeta>& field_order_;
};

// Helper to convert C++ Value to Python object
inline py::object value_to_py(const vdb::Value& val) {
  if (std::holds_alternative<std::monostate>(val))
    return py::none();
  if (std::holds_alternative<int64_t>(val))
    return py::cast(std::get<int64_t>(val));
  if (std::holds_alternative<uint64_t>(val))
    return py::cast(std::get<uint64_t>(val));
  if (std::holds_alternative<float>(val))
    return py::cast(std::get<float>(val));
  if (std::holds_alternative<bool>(val))
    return py::cast(std::get<bool>(val));
  if (std::holds_alternative<std::string>(val)) {
    return py::cast(std::get<std::string>(val));
  }
  if (std::holds_alternative<std::vector<int64_t>>(val))
    return py::cast(std::get<std::vector<int64_t>>(val));
  if (std::holds_alternative<std::vector<float>>(val))
    return py::cast(std::get<std::vector<float>>(val));
  if (std::holds_alternative<std::vector<std::string>>(val))
    return py::cast(std::get<std::vector<std::string>>(val));

  return py::none();
}
