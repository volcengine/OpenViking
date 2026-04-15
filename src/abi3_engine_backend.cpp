// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#define Py_LIMITED_API 0x030A0000
#include <Python.h>

#include <memory>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>

#include "common/log_utils.h"
#include "index/common_structs.h"
#include "index/index_engine.h"
#include "store/bytes_row.h"
#include "store/kv_store.h"
#include "store/persist_store.h"
#include "store/volatile_store.h"

namespace vdb = vectordb;

namespace {

constexpr const char* kIndexCapsuleName = "openviking.vectordb.IndexEngine";
constexpr const char* kStoreCapsuleName = "openviking.vectordb.KVStore";
constexpr const char* kSchemaCapsuleName = "openviking.vectordb.Schema";
constexpr const char* kBytesRowCapsuleName = "openviking.vectordb.BytesRow";

struct SchemaHandle {
  std::shared_ptr<vdb::Schema> schema;
};

struct BytesRowHandle {
  std::shared_ptr<vdb::Schema> schema;
  std::shared_ptr<vdb::BytesRow> bytes_row;
};

void raise_type_error(const std::string& message) {
  PyErr_SetString(PyExc_TypeError, message.c_str());
}

void raise_value_error(const std::string& message) {
  PyErr_SetString(PyExc_ValueError, message.c_str());
}

void raise_runtime_error(const std::string& message) {
  PyErr_SetString(PyExc_RuntimeError, message.c_str());
}

template <typename Func>
auto call_without_gil(Func&& func) -> decltype(func()) {
  PyThreadState* save = PyEval_SaveThread();
  try {
    if constexpr (std::is_void_v<decltype(func())>) {
      func();
      PyEval_RestoreThread(save);
    } else {
      auto result = func();
      PyEval_RestoreThread(save);
      return result;
    }
  } catch (...) {
    PyEval_RestoreThread(save);
    throw;
  }
}

bool get_named_value(PyObject* obj, const char* name, PyObject** out, bool* found) {
  *out = nullptr;
  *found = false;

  if (PyDict_Check(obj)) {
    PyObject* value = PyDict_GetItemString(obj, name);
    if (value == nullptr) {
      return true;
    }
    Py_INCREF(value);
    *out = value;
    *found = true;
    return true;
  }

  PyObject* value = PyObject_GetAttrString(obj, name);
  if (value == nullptr) {
    if (PyErr_ExceptionMatches(PyExc_AttributeError)) {
      PyErr_Clear();
      return true;
    }
    return false;
  }

  *out = value;
  *found = true;
  return true;
}

bool py_to_string(PyObject* obj, std::string* out, bool allow_bytes) {
  if (PyUnicode_Check(obj)) {
    Py_ssize_t size = 0;
    const char* data = PyUnicode_AsUTF8AndSize(obj, &size);
    if (data == nullptr) {
      return false;
    }
    out->assign(data, static_cast<size_t>(size));
    return true;
  }

  if (allow_bytes && PyBytes_Check(obj)) {
    char* data = nullptr;
    Py_ssize_t size = 0;
    if (PyBytes_AsStringAndSize(obj, &data, &size) < 0) {
      return false;
    }
    out->assign(data, static_cast<size_t>(size));
    return true;
  }

  raise_type_error("Expected str" + std::string(allow_bytes ? " or bytes" : ""));
  return false;
}

bool py_to_uint64(PyObject* obj, uint64_t* out) {
  const unsigned long long value = PyLong_AsUnsignedLongLong(obj);
  if (PyErr_Occurred() != nullptr) {
    return false;
  }
  *out = static_cast<uint64_t>(value);
  return true;
}

bool py_to_uint32(PyObject* obj, uint32_t* out) {
  const unsigned long value = PyLong_AsUnsignedLong(obj);
  if (PyErr_Occurred() != nullptr) {
    return false;
  }
  *out = static_cast<uint32_t>(value);
  return true;
}

bool py_to_float(PyObject* obj, float* out) {
  const double value = PyFloat_AsDouble(obj);
  if (PyErr_Occurred() != nullptr) {
    return false;
  }
  *out = static_cast<float>(value);
  return true;
}

bool py_to_int64(PyObject* obj, int64_t* out) {
  const long long value = PyLong_AsLongLong(obj);
  if (PyErr_Occurred() != nullptr) {
    return false;
  }
  *out = static_cast<int64_t>(value);
  return true;
}

bool py_to_bool(PyObject* obj, bool* out) {
  const int value = PyObject_IsTrue(obj);
  if (value < 0) {
    return false;
  }
  *out = value != 0;
  return true;
}

bool py_to_float_vector(PyObject* obj, std::vector<float>* out) {
  const Py_ssize_t size = PySequence_Size(obj);
  if (size < 0) {
    raise_type_error("Expected a sequence of floats");
    return false;
  }

  out->clear();
  out->reserve(static_cast<size_t>(size));
  for (Py_ssize_t i = 0; i < size; ++i) {
    PyObject* item = PySequence_GetItem(obj, i);
    if (item == nullptr) {
      return false;
    }
    float value = 0.0f;
    const bool ok = py_to_float(item, &value);
    Py_DECREF(item);
    if (!ok) {
      return false;
    }
    out->push_back(value);
  }
  return true;
}

bool py_to_string_vector(PyObject* obj, std::vector<std::string>* out) {
  const Py_ssize_t size = PySequence_Size(obj);
  if (size < 0) {
    raise_type_error("Expected a sequence of strings");
    return false;
  }

  out->clear();
  out->reserve(static_cast<size_t>(size));
  for (Py_ssize_t i = 0; i < size; ++i) {
    PyObject* item = PySequence_GetItem(obj, i);
    if (item == nullptr) {
      return false;
    }
    std::string value;
    const bool ok = py_to_string(item, &value, false);
    Py_DECREF(item);
    if (!ok) {
      return false;
    }
    out->push_back(std::move(value));
  }
  return true;
}

bool py_to_int64_vector(PyObject* obj, std::vector<int64_t>* out) {
  const Py_ssize_t size = PySequence_Size(obj);
  if (size < 0) {
    raise_type_error("Expected a sequence of integers");
    return false;
  }

  out->clear();
  out->reserve(static_cast<size_t>(size));
  for (Py_ssize_t i = 0; i < size; ++i) {
    PyObject* item = PySequence_GetItem(obj, i);
    if (item == nullptr) {
      return false;
    }
    int64_t value = 0;
    const bool ok = py_to_int64(item, &value);
    Py_DECREF(item);
    if (!ok) {
      return false;
    }
    out->push_back(value);
  }
  return true;
}

PyObject* float_vector_to_py(const std::vector<float>& values) {
  PyObject* list = PyList_New(static_cast<Py_ssize_t>(values.size()));
  if (list == nullptr) {
    return nullptr;
  }
  for (Py_ssize_t i = 0; i < static_cast<Py_ssize_t>(values.size()); ++i) {
    PyObject* item = PyFloat_FromDouble(values[static_cast<size_t>(i)]);
    if (item == nullptr) {
      Py_DECREF(list);
      return nullptr;
    }
    PyList_SetItem(list, i, item);
  }
  return list;
}

PyObject* string_vector_to_py(const std::vector<std::string>& values) {
  PyObject* list = PyList_New(static_cast<Py_ssize_t>(values.size()));
  if (list == nullptr) {
    return nullptr;
  }
  for (Py_ssize_t i = 0; i < static_cast<Py_ssize_t>(values.size()); ++i) {
    const auto& value = values[static_cast<size_t>(i)];
    PyObject* item = PyUnicode_FromStringAndSize(value.data(), static_cast<Py_ssize_t>(value.size()));
    if (item == nullptr) {
      Py_DECREF(list);
      return nullptr;
    }
    PyList_SetItem(list, i, item);
  }
  return list;
}

PyObject* uint64_vector_to_py(const std::vector<uint64_t>& values) {
  PyObject* list = PyList_New(static_cast<Py_ssize_t>(values.size()));
  if (list == nullptr) {
    return nullptr;
  }
  for (Py_ssize_t i = 0; i < static_cast<Py_ssize_t>(values.size()); ++i) {
    PyObject* item = PyLong_FromUnsignedLongLong(values[static_cast<size_t>(i)]);
    if (item == nullptr) {
      Py_DECREF(list);
      return nullptr;
    }
    PyList_SetItem(list, i, item);
  }
  return list;
}

PyObject* value_to_py(const vdb::Value& value, vdb::FieldType field_type) {
  switch (field_type) {
    case vdb::FieldType::INT64:
      if (std::holds_alternative<int64_t>(value)) {
        return PyLong_FromLongLong(std::get<int64_t>(value));
      }
      break;
    case vdb::FieldType::UINT64:
      if (std::holds_alternative<uint64_t>(value)) {
        return PyLong_FromUnsignedLongLong(std::get<uint64_t>(value));
      }
      break;
    case vdb::FieldType::FLOAT32:
      if (std::holds_alternative<float>(value)) {
        return PyFloat_FromDouble(std::get<float>(value));
      }
      break;
    case vdb::FieldType::BOOLEAN:
      if (std::holds_alternative<bool>(value)) {
        if (std::get<bool>(value)) {
          Py_RETURN_TRUE;
        }
        Py_RETURN_FALSE;
      }
      break;
    case vdb::FieldType::STRING:
      if (std::holds_alternative<std::string>(value)) {
        const auto& text = std::get<std::string>(value);
        return PyUnicode_FromStringAndSize(text.data(), static_cast<Py_ssize_t>(text.size()));
      }
      break;
    case vdb::FieldType::BINARY:
      if (std::holds_alternative<std::string>(value)) {
        const auto& blob = std::get<std::string>(value);
        return PyBytes_FromStringAndSize(blob.data(), static_cast<Py_ssize_t>(blob.size()));
      }
      break;
    case vdb::FieldType::LIST_INT64:
      if (std::holds_alternative<std::vector<int64_t>>(value)) {
        const auto& items = std::get<std::vector<int64_t>>(value);
        PyObject* list = PyList_New(static_cast<Py_ssize_t>(items.size()));
        if (list == nullptr) {
          return nullptr;
        }
        for (Py_ssize_t i = 0; i < static_cast<Py_ssize_t>(items.size()); ++i) {
          PyObject* item = PyLong_FromLongLong(items[static_cast<size_t>(i)]);
          if (item == nullptr) {
            Py_DECREF(list);
            return nullptr;
          }
          PyList_SetItem(list, i, item);
        }
        return list;
      }
      break;
    case vdb::FieldType::LIST_STRING:
      if (std::holds_alternative<std::vector<std::string>>(value)) {
        return string_vector_to_py(std::get<std::vector<std::string>>(value));
      }
      break;
    case vdb::FieldType::LIST_FLOAT32:
      if (std::holds_alternative<std::vector<float>>(value)) {
        return float_vector_to_py(std::get<std::vector<float>>(value));
      }
      break;
  }

  Py_RETURN_NONE;
}

bool py_to_field_value(PyObject* obj, vdb::FieldType data_type, vdb::Value* out) {
  switch (data_type) {
    case vdb::FieldType::INT64: {
      int64_t value = 0;
      if (!py_to_int64(obj, &value)) {
        return false;
      }
      *out = value;
      return true;
    }
    case vdb::FieldType::UINT64: {
      uint64_t value = 0;
      if (!py_to_uint64(obj, &value)) {
        return false;
      }
      *out = value;
      return true;
    }
    case vdb::FieldType::FLOAT32: {
      float value = 0.0f;
      if (!py_to_float(obj, &value)) {
        return false;
      }
      *out = value;
      return true;
    }
    case vdb::FieldType::STRING:
    case vdb::FieldType::BINARY: {
      std::string value;
      if (!py_to_string(obj, &value, true)) {
        return false;
      }
      *out = std::move(value);
      return true;
    }
    case vdb::FieldType::BOOLEAN: {
      bool value = false;
      if (!py_to_bool(obj, &value)) {
        return false;
      }
      *out = value;
      return true;
    }
    case vdb::FieldType::LIST_INT64: {
      std::vector<int64_t> values;
      if (!py_to_int64_vector(obj, &values)) {
        return false;
      }
      *out = std::move(values);
      return true;
    }
    case vdb::FieldType::LIST_STRING: {
      std::vector<std::string> values;
      if (!py_to_string_vector(obj, &values)) {
        return false;
      }
      *out = std::move(values);
      return true;
    }
    case vdb::FieldType::LIST_FLOAT32: {
      std::vector<float> values;
      if (!py_to_float_vector(obj, &values)) {
        return false;
      }
      *out = std::move(values);
      return true;
    }
  }

  raise_type_error("Unsupported field type");
  return false;
}

template <typename T>
T* capsule_to_ptr(PyObject* capsule, const char* capsule_name) {
  if (!PyCapsule_CheckExact(capsule)) {
    raise_type_error("Expected capsule handle");
    return nullptr;
  }
  return static_cast<T*>(PyCapsule_GetPointer(capsule, capsule_name));
}

void schema_capsule_destructor(PyObject* capsule) {
  auto* ptr = static_cast<SchemaHandle*>(PyCapsule_GetPointer(capsule, kSchemaCapsuleName));
  delete ptr;
}

void bytes_row_capsule_destructor(PyObject* capsule) {
  auto* ptr = static_cast<BytesRowHandle*>(PyCapsule_GetPointer(capsule, kBytesRowCapsuleName));
  delete ptr;
}

void index_capsule_destructor(PyObject* capsule) {
  auto* ptr = static_cast<vdb::IndexEngine*>(PyCapsule_GetPointer(capsule, kIndexCapsuleName));
  delete ptr;
}

void store_capsule_destructor(PyObject* capsule) {
  auto* ptr = static_cast<vdb::KVStore*>(PyCapsule_GetPointer(capsule, kStoreCapsuleName));
  delete ptr;
}

bool py_to_field_type(PyObject* obj, vdb::FieldType* out) {
  const long value = PyLong_AsLong(obj);
  if (PyErr_Occurred() != nullptr) {
    return false;
  }

  switch (value) {
    case 0:
      *out = vdb::FieldType::INT64;
      return true;
    case 1:
      *out = vdb::FieldType::UINT64;
      return true;
    case 2:
      *out = vdb::FieldType::FLOAT32;
      return true;
    case 3:
      *out = vdb::FieldType::STRING;
      return true;
    case 4:
      *out = vdb::FieldType::BINARY;
      return true;
    case 5:
      *out = vdb::FieldType::BOOLEAN;
      return true;
    case 6:
      *out = vdb::FieldType::LIST_INT64;
      return true;
    case 7:
      *out = vdb::FieldType::LIST_STRING;
      return true;
    case 8:
      *out = vdb::FieldType::LIST_FLOAT32;
      return true;
    default:
      raise_value_error("Unsupported field type");
      return false;
  }
}

bool parse_schema_fields(PyObject* fields_obj, std::vector<vdb::FieldDef>* fields) {
  const Py_ssize_t size = PySequence_Size(fields_obj);
  if (size < 0) {
    raise_type_error("Schema fields must be a sequence");
    return false;
  }

  fields->clear();
  fields->reserve(static_cast<size_t>(size));

  for (Py_ssize_t i = 0; i < size; ++i) {
    PyObject* item = PySequence_GetItem(fields_obj, i);
    if (item == nullptr) {
      return false;
    }
    if (!PyDict_Check(item)) {
      Py_DECREF(item);
      raise_type_error("Each schema field must be a dict");
      return false;
    }

    vdb::FieldDef field;
    PyObject* name_obj = PyDict_GetItemString(item, "name");
    PyObject* type_obj = PyDict_GetItemString(item, "data_type");
    PyObject* id_obj = PyDict_GetItemString(item, "id");
    if (name_obj == nullptr || type_obj == nullptr || id_obj == nullptr) {
      Py_DECREF(item);
      raise_value_error("Schema field definition must contain name, data_type, and id");
      return false;
    }

    if (!py_to_string(name_obj, &field.name, false) || !py_to_field_type(type_obj, &field.data_type)) {
      Py_DECREF(item);
      return false;
    }

    const long field_id = PyLong_AsLong(id_obj);
    if (PyErr_Occurred() != nullptr) {
      Py_DECREF(item);
      return false;
    }
    field.id = static_cast<int>(field_id);

    PyObject* default_value = PyDict_GetItemString(item, "default_value");
    if (default_value != nullptr && default_value != Py_None) {
      if (!py_to_field_value(default_value, field.data_type, &field.default_value)) {
        PyErr_Clear();
        field.default_value = std::monostate{};
      }
    } else {
      field.default_value = std::monostate{};
    }

    fields->push_back(std::move(field));
    Py_DECREF(item);
  }

  return true;
}

bool row_object_to_values(PyObject* obj,
                          const std::vector<vdb::FieldMeta>& field_order,
                          std::vector<vdb::Value>* out) {
  out->assign(field_order.size(), std::monostate{});
  for (size_t i = 0; i < field_order.size(); ++i) {
    const auto& meta = field_order[i];
    PyObject* value = nullptr;
    bool found = false;
    if (!get_named_value(obj, meta.name.c_str(), &value, &found)) {
      return false;
    }
    if (!found || value == Py_None) {
      Py_XDECREF(value);
      continue;
    }

    vdb::Value parsed;
    const bool ok = py_to_field_value(value, meta.data_type, &parsed);
    Py_DECREF(value);
    if (!ok) {
      return false;
    }
    (*out)[i] = std::move(parsed);
  }
  return true;
}

bool parse_add_request(PyObject* obj, vdb::AddDataRequest* request) {
  PyObject* value = nullptr;
  bool found = false;

  if (!get_named_value(obj, "label", &value, &found)) {
    return false;
  }
  if (found && !py_to_uint64(value, &request->label)) {
    Py_DECREF(value);
    return false;
  }
  Py_XDECREF(value);

  if (!get_named_value(obj, "vector", &value, &found)) {
    return false;
  }
  if (found && !py_to_float_vector(value, &request->vector)) {
    Py_DECREF(value);
    return false;
  }
  Py_XDECREF(value);

  if (!get_named_value(obj, "sparse_raw_terms", &value, &found)) {
    return false;
  }
  if (found && !py_to_string_vector(value, &request->sparse_raw_terms)) {
    Py_DECREF(value);
    return false;
  }
  Py_XDECREF(value);

  if (!get_named_value(obj, "sparse_values", &value, &found)) {
    return false;
  }
  if (found && !py_to_float_vector(value, &request->sparse_values)) {
    Py_DECREF(value);
    return false;
  }
  Py_XDECREF(value);

  if (!get_named_value(obj, "fields_str", &value, &found)) {
    return false;
  }
  if (found && !py_to_string(value, &request->fields_str, true)) {
    Py_DECREF(value);
    return false;
  }
  Py_XDECREF(value);

  if (!get_named_value(obj, "old_fields_str", &value, &found)) {
    return false;
  }
  if (found && !py_to_string(value, &request->old_fields_str, true)) {
    Py_DECREF(value);
    return false;
  }
  Py_XDECREF(value);

  return true;
}

bool parse_delete_request(PyObject* obj, vdb::DeleteDataRequest* request) {
  PyObject* value = nullptr;
  bool found = false;

  if (!get_named_value(obj, "label", &value, &found)) {
    return false;
  }
  if (found && !py_to_uint64(value, &request->label)) {
    Py_DECREF(value);
    return false;
  }
  Py_XDECREF(value);

  if (!get_named_value(obj, "old_fields_str", &value, &found)) {
    return false;
  }
  if (found && !py_to_string(value, &request->old_fields_str, true)) {
    Py_DECREF(value);
    return false;
  }
  Py_XDECREF(value);

  return true;
}

bool parse_search_request(PyObject* obj, vdb::SearchRequest* request) {
  PyObject* value = nullptr;
  bool found = false;

  if (!get_named_value(obj, "query", &value, &found)) {
    return false;
  }
  if (found && !py_to_float_vector(value, &request->query)) {
    Py_DECREF(value);
    return false;
  }
  Py_XDECREF(value);

  if (!get_named_value(obj, "sparse_raw_terms", &value, &found)) {
    return false;
  }
  if (found && !py_to_string_vector(value, &request->sparse_raw_terms)) {
    Py_DECREF(value);
    return false;
  }
  Py_XDECREF(value);

  if (!get_named_value(obj, "sparse_values", &value, &found)) {
    return false;
  }
  if (found && !py_to_float_vector(value, &request->sparse_values)) {
    Py_DECREF(value);
    return false;
  }
  Py_XDECREF(value);

  if (!get_named_value(obj, "topk", &value, &found)) {
    return false;
  }
  if (found && !py_to_uint32(value, &request->topk)) {
    Py_DECREF(value);
    return false;
  }
  Py_XDECREF(value);

  if (!get_named_value(obj, "dsl", &value, &found)) {
    return false;
  }
  if (found && !py_to_string(value, &request->dsl, false)) {
    Py_DECREF(value);
    return false;
  }
  Py_XDECREF(value);

  return true;
}

bool parse_storage_op(PyObject* obj, vdb::StorageOp* op) {
  PyObject* value = nullptr;
  bool found = false;

  if (!get_named_value(obj, "type", &value, &found)) {
    return false;
  }
  long type_value = 0;
  if (found) {
    type_value = PyLong_AsLong(value);
    if (PyErr_Occurred() != nullptr) {
      Py_DECREF(value);
      return false;
    }
    if (type_value != static_cast<long>(vdb::StorageOp::PUT_OP) &&
        type_value != static_cast<long>(vdb::StorageOp::DELETE_OP)) {
      Py_DECREF(value);
      raise_value_error("Invalid storage op type");
      return false;
    }
  }
  Py_XDECREF(value);
  op->type = type_value == static_cast<long>(vdb::StorageOp::DELETE_OP)
      ? vdb::StorageOp::DELETE_OP
      : vdb::StorageOp::PUT_OP;

  if (!get_named_value(obj, "key", &value, &found)) {
    return false;
  }
  if (!found) {
    raise_value_error("Storage op key is required");
    return false;
  }
  if (!py_to_string(value, &op->key, false)) {
    Py_XDECREF(value);
    return false;
  }
  Py_DECREF(value);

  if (!get_named_value(obj, "value", &value, &found)) {
    return false;
  }
  if (found && !py_to_string(value, &op->value, true)) {
    Py_DECREF(value);
    return false;
  }
  Py_XDECREF(value);

  return true;
}

template <typename RequestT>
bool parse_request_list(PyObject* obj,
                        bool (*parse_item)(PyObject*, RequestT*),
                        std::vector<RequestT>* out) {
  const Py_ssize_t size = PySequence_Size(obj);
  if (size < 0) {
    raise_type_error("Expected a sequence");
    return false;
  }

  out->clear();
  out->reserve(static_cast<size_t>(size));
  for (Py_ssize_t i = 0; i < size; ++i) {
    PyObject* item = PySequence_GetItem(obj, i);
    if (item == nullptr) {
      return false;
    }
    RequestT request;
    const bool ok = parse_item(item, &request);
    Py_DECREF(item);
    if (!ok) {
      return false;
    }
    out->push_back(std::move(request));
  }

  return true;
}

bool parse_string_list(PyObject* obj, std::vector<std::string>* out) {
  return py_to_string_vector(obj, out);
}

bool parse_binary_list(PyObject* obj, std::vector<std::string>* out) {
  const Py_ssize_t size = PySequence_Size(obj);
  if (size < 0) {
    raise_type_error("Expected a sequence of bytes");
    return false;
  }
  out->clear();
  out->reserve(static_cast<size_t>(size));
  for (Py_ssize_t i = 0; i < size; ++i) {
    PyObject* item = PySequence_GetItem(obj, i);
    if (item == nullptr) {
      return false;
    }
    std::string value;
    const bool ok = py_to_string(item, &value, true);
    Py_DECREF(item);
    if (!ok) {
      return false;
    }
    out->push_back(std::move(value));
  }
  return true;
}

PyObject* py_new_schema(PyObject*, PyObject* args) {
  PyObject* fields_obj = nullptr;
  if (!PyArg_ParseTuple(args, "O", &fields_obj)) {
    return nullptr;
  }

  std::vector<vdb::FieldDef> fields;
  if (!parse_schema_fields(fields_obj, &fields)) {
    return nullptr;
  }

  try {
    auto* handle = new SchemaHandle{std::make_shared<vdb::Schema>(fields)};
    return PyCapsule_New(handle, kSchemaCapsuleName, schema_capsule_destructor);
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_schema_get_total_byte_length(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  if (!PyArg_ParseTuple(args, "O", &capsule)) {
    return nullptr;
  }

  auto* handle = capsule_to_ptr<SchemaHandle>(capsule, kSchemaCapsuleName);
  if (handle == nullptr) {
    return nullptr;
  }

  return PyLong_FromLong(handle->schema->get_total_byte_length());
}

PyObject* py_new_bytes_row(PyObject*, PyObject* args) {
  PyObject* schema_capsule = nullptr;
  if (!PyArg_ParseTuple(args, "O", &schema_capsule)) {
    return nullptr;
  }

  auto* schema_handle = capsule_to_ptr<SchemaHandle>(schema_capsule, kSchemaCapsuleName);
  if (schema_handle == nullptr) {
    return nullptr;
  }

  auto* handle = new BytesRowHandle{
      schema_handle->schema,
      std::make_shared<vdb::BytesRow>(schema_handle->schema),
  };
  return PyCapsule_New(handle, kBytesRowCapsuleName, bytes_row_capsule_destructor);
}

PyObject* py_bytes_row_serialize(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  PyObject* row_obj = nullptr;
  if (!PyArg_ParseTuple(args, "OO", &capsule, &row_obj)) {
    return nullptr;
  }

  auto* handle = capsule_to_ptr<BytesRowHandle>(capsule, kBytesRowCapsuleName);
  if (handle == nullptr) {
    return nullptr;
  }

  std::vector<vdb::Value> row_values;
  if (!row_object_to_values(row_obj, handle->schema->get_field_order(), &row_values)) {
    return nullptr;
  }

  try {
    const std::string payload =
        call_without_gil([&]() { return handle->bytes_row->serialize(row_values); });
    return PyBytes_FromStringAndSize(payload.data(), static_cast<Py_ssize_t>(payload.size()));
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_bytes_row_serialize_batch(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  PyObject* rows_obj = nullptr;
  if (!PyArg_ParseTuple(args, "OO", &capsule, &rows_obj)) {
    return nullptr;
  }

  auto* handle = capsule_to_ptr<BytesRowHandle>(capsule, kBytesRowCapsuleName);
  if (handle == nullptr) {
    return nullptr;
  }

  const Py_ssize_t size = PySequence_Size(rows_obj);
  if (size < 0) {
    raise_type_error("Expected a sequence of rows");
    return nullptr;
  }

  PyObject* list = PyList_New(size);
  if (list == nullptr) {
    return nullptr;
  }

  for (Py_ssize_t i = 0; i < size; ++i) {
    PyObject* row_obj = PySequence_GetItem(rows_obj, i);
    if (row_obj == nullptr) {
      Py_DECREF(list);
      return nullptr;
    }

    std::vector<vdb::Value> row_values;
    const bool ok = row_object_to_values(row_obj, handle->schema->get_field_order(), &row_values);
    Py_DECREF(row_obj);
    if (!ok) {
      Py_DECREF(list);
      return nullptr;
    }

    try {
      const std::string payload =
          call_without_gil([&]() { return handle->bytes_row->serialize(row_values); });
      PyObject* item = PyBytes_FromStringAndSize(payload.data(), static_cast<Py_ssize_t>(payload.size()));
      if (item == nullptr) {
        Py_DECREF(list);
        return nullptr;
      }
      PyList_SetItem(list, i, item);
    } catch (const std::exception& exc) {
      Py_DECREF(list);
      raise_runtime_error(exc.what());
      return nullptr;
    }
  }

  return list;
}

PyObject* py_bytes_row_deserialize(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  PyObject* payload_obj = nullptr;
  if (!PyArg_ParseTuple(args, "OO", &capsule, &payload_obj)) {
    return nullptr;
  }

  auto* handle = capsule_to_ptr<BytesRowHandle>(capsule, kBytesRowCapsuleName);
  if (handle == nullptr) {
    return nullptr;
  }

  std::string payload;
  if (!py_to_string(payload_obj, &payload, true)) {
    return nullptr;
  }

  PyObject* result = PyDict_New();
  if (result == nullptr) {
    return nullptr;
  }

  try {
    for (const auto& meta : handle->schema->get_field_order()) {
      const vdb::Value value =
          call_without_gil([&]() { return handle->bytes_row->deserialize_field(payload, meta.name); });
      if (std::holds_alternative<std::monostate>(value)) {
        continue;
      }

      PyObject* py_value = value_to_py(value, meta.data_type);
      if (py_value == nullptr) {
        Py_DECREF(result);
        return nullptr;
      }
      if (PyDict_SetItemString(result, meta.name.c_str(), py_value) < 0) {
        Py_DECREF(py_value);
        Py_DECREF(result);
        return nullptr;
      }
      Py_DECREF(py_value);
    }
  } catch (const std::exception& exc) {
    Py_DECREF(result);
    raise_runtime_error(exc.what());
    return nullptr;
  }

  return result;
}

PyObject* py_bytes_row_deserialize_field(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  PyObject* payload_obj = nullptr;
  const char* field_name = nullptr;
  if (!PyArg_ParseTuple(args, "OOs", &capsule, &payload_obj, &field_name)) {
    return nullptr;
  }

  auto* handle = capsule_to_ptr<BytesRowHandle>(capsule, kBytesRowCapsuleName);
  if (handle == nullptr) {
    return nullptr;
  }

  std::string payload;
  if (!py_to_string(payload_obj, &payload, true)) {
    return nullptr;
  }

  const auto* meta = handle->schema->get_field_meta(field_name);
  if (meta == nullptr) {
    raise_value_error("Field does not exist in schema");
    return nullptr;
  }

  try {
    const vdb::Value value =
        call_without_gil([&]() { return handle->bytes_row->deserialize_field(payload, field_name); });
    return value_to_py(value, meta->data_type);
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* build_search_result(const vdb::SearchResult& result) {
  PyObject* payload = PyDict_New();
  if (payload == nullptr) {
    return nullptr;
  }

  PyObject* result_num = PyLong_FromUnsignedLong(result.result_num);
  PyObject* labels = uint64_vector_to_py(result.labels);
  PyObject* scores = float_vector_to_py(result.scores);
  PyObject* extra_json = PyUnicode_FromStringAndSize(
      result.extra_json.data(), static_cast<Py_ssize_t>(result.extra_json.size()));
  if (result_num == nullptr || labels == nullptr || scores == nullptr ||
      extra_json == nullptr) {
    Py_XDECREF(result_num);
    Py_XDECREF(labels);
    Py_XDECREF(scores);
    Py_XDECREF(extra_json);
    Py_DECREF(payload);
    return nullptr;
  }

  PyDict_SetItemString(payload, "result_num", result_num);
  PyDict_SetItemString(payload, "labels", labels);
  PyDict_SetItemString(payload, "scores", scores);
  PyDict_SetItemString(payload, "extra_json", extra_json);
  Py_DECREF(result_num);
  Py_DECREF(labels);
  Py_DECREF(scores);
  Py_DECREF(extra_json);
  return payload;
}

PyObject* build_state_result(const vdb::StateResult& result) {
  PyObject* payload = PyDict_New();
  if (payload == nullptr) {
    return nullptr;
  }

  PyObject* update_ts = PyLong_FromUnsignedLongLong(result.update_timestamp);
  PyObject* element_count = PyLong_FromUnsignedLongLong(result.element_count);
  if (update_ts == nullptr || element_count == nullptr) {
    Py_XDECREF(update_ts);
    Py_XDECREF(element_count);
    Py_DECREF(payload);
    return nullptr;
  }

  PyDict_SetItemString(payload, "update_timestamp", update_ts);
  PyDict_SetItemString(payload, "element_count", element_count);
  Py_DECREF(update_ts);
  Py_DECREF(element_count);
  return payload;
}

PyObject* py_init_logging(PyObject*, PyObject* args, PyObject* kwargs) {
  const char* log_level = nullptr;
  const char* log_output = nullptr;
  const char* log_format = "[%Y-%m-%d %H:%M:%S.%e] [%l] %v";
  static const char* keywords[] = {"log_level", "log_output", "log_format", nullptr};

  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "ss|s", const_cast<char**>(keywords),
                                   &log_level, &log_output, &log_format)) {
    return nullptr;
  }

  try {
    vdb::init_logging(log_level, log_output, log_format);
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }

  Py_RETURN_NONE;
}

PyObject* py_new_index_engine(PyObject*, PyObject* args) {
  const char* path_or_json = nullptr;
  if (!PyArg_ParseTuple(args, "s", &path_or_json)) {
    return nullptr;
  }

  try {
    return PyCapsule_New(new vdb::IndexEngine(path_or_json), kIndexCapsuleName,
                         index_capsule_destructor);
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_index_engine_add_data(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  PyObject* items = nullptr;
  if (!PyArg_ParseTuple(args, "OO", &capsule, &items)) {
    return nullptr;
  }

  auto* engine = capsule_to_ptr<vdb::IndexEngine>(capsule, kIndexCapsuleName);
  if (engine == nullptr) {
    return nullptr;
  }

  std::vector<vdb::AddDataRequest> requests;
  if (!parse_request_list(items, parse_add_request, &requests)) {
    return nullptr;
  }

  try {
    const int result = call_without_gil([&]() { return engine->add_data(requests); });
    return PyLong_FromLong(result);
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_index_engine_delete_data(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  PyObject* items = nullptr;
  if (!PyArg_ParseTuple(args, "OO", &capsule, &items)) {
    return nullptr;
  }

  auto* engine = capsule_to_ptr<vdb::IndexEngine>(capsule, kIndexCapsuleName);
  if (engine == nullptr) {
    return nullptr;
  }

  std::vector<vdb::DeleteDataRequest> requests;
  if (!parse_request_list(items, parse_delete_request, &requests)) {
    return nullptr;
  }

  try {
    const int result = call_without_gil([&]() { return engine->delete_data(requests); });
    return PyLong_FromLong(result);
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_index_engine_search(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  PyObject* request_obj = nullptr;
  if (!PyArg_ParseTuple(args, "OO", &capsule, &request_obj)) {
    return nullptr;
  }

  auto* engine = capsule_to_ptr<vdb::IndexEngine>(capsule, kIndexCapsuleName);
  if (engine == nullptr) {
    return nullptr;
  }

  vdb::SearchRequest request;
  if (!parse_search_request(request_obj, &request)) {
    return nullptr;
  }

  try {
    const vdb::SearchResult result = call_without_gil([&]() { return engine->search(request); });
    return build_search_result(result);
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_index_engine_search_batch(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  PyObject* request_list = nullptr;
  if (!PyArg_ParseTuple(args, "OO", &capsule, &request_list)) {
    return nullptr;
  }

  auto* engine = capsule_to_ptr<vdb::IndexEngine>(capsule, kIndexCapsuleName);
  if (engine == nullptr) {
    return nullptr;
  }

  if (!PyList_Check(request_list)) {
    raise_runtime_error("search_batch requires a list of SearchRequest objects");
    return nullptr;
  }

  Py_ssize_t nq = PyList_Size(request_list);
  std::vector<vdb::SearchRequest> requests(static_cast<size_t>(nq));
  for (Py_ssize_t i = 0; i < nq; ++i) {
    PyObject* req_obj = PyList_GetItem(request_list, i);
    if (!parse_search_request(req_obj, &requests[static_cast<size_t>(i)])) {
      return nullptr;
    }
  }

  try {
    auto results = call_without_gil([&]() {
      return engine->search_batch(requests);
    });

    PyObject* result_list = PyList_New(static_cast<Py_ssize_t>(results.size()));
    if (result_list == nullptr) {
      return nullptr;
    }

    for (size_t i = 0; i < results.size(); ++i) {
      PyObject* result = build_search_result(results[i]);
      if (result == nullptr) {
        Py_DECREF(result_list);
        return nullptr;
      }
      PyList_SetItem(result_list, static_cast<Py_ssize_t>(i), result);
    }
    return result_list;
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_index_engine_dump(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  const char* path = nullptr;
  if (!PyArg_ParseTuple(args, "Os", &capsule, &path)) {
    return nullptr;
  }

  auto* engine = capsule_to_ptr<vdb::IndexEngine>(capsule, kIndexCapsuleName);
  if (engine == nullptr) {
    return nullptr;
  }

  try {
    const int64_t result = call_without_gil([&]() { return engine->dump(path); });
    return PyLong_FromLongLong(result);
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_index_engine_get_state(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  if (!PyArg_ParseTuple(args, "O", &capsule)) {
    return nullptr;
  }

  auto* engine = capsule_to_ptr<vdb::IndexEngine>(capsule, kIndexCapsuleName);
  if (engine == nullptr) {
    return nullptr;
  }

  try {
    const vdb::StateResult result = call_without_gil([&]() { return engine->get_state(); });
    return build_state_result(result);
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_new_persist_store(PyObject*, PyObject* args) {
  const char* path = nullptr;
  if (!PyArg_ParseTuple(args, "s", &path)) {
    return nullptr;
  }

  try {
    return PyCapsule_New(new vdb::PersistStore(path), kStoreCapsuleName,
                         store_capsule_destructor);
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_new_volatile_store(PyObject*, PyObject*) {
  try {
    return PyCapsule_New(new vdb::VolatileStore(), kStoreCapsuleName,
                         store_capsule_destructor);
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_store_exec_op(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  PyObject* ops_obj = nullptr;
  if (!PyArg_ParseTuple(args, "OO", &capsule, &ops_obj)) {
    return nullptr;
  }

  auto* store = capsule_to_ptr<vdb::KVStore>(capsule, kStoreCapsuleName);
  if (store == nullptr) {
    return nullptr;
  }

  std::vector<vdb::StorageOp> ops;
  if (!parse_request_list(ops_obj, parse_storage_op, &ops)) {
    return nullptr;
  }

  try {
    const int result = call_without_gil([&]() { return store->exec_op(ops); });
    return PyLong_FromLong(result);
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_store_get_data(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  PyObject* keys_obj = nullptr;
  if (!PyArg_ParseTuple(args, "OO", &capsule, &keys_obj)) {
    return nullptr;
  }

  auto* store = capsule_to_ptr<vdb::KVStore>(capsule, kStoreCapsuleName);
  if (store == nullptr) {
    return nullptr;
  }

  std::vector<std::string> keys;
  if (!parse_string_list(keys_obj, &keys)) {
    return nullptr;
  }

  try {
    const auto values = call_without_gil([&]() { return store->get_data(keys); });
    PyObject* list = PyList_New(static_cast<Py_ssize_t>(values.size()));
    if (list == nullptr) {
      return nullptr;
    }
    for (Py_ssize_t i = 0; i < static_cast<Py_ssize_t>(values.size()); ++i) {
      const auto& value = values[static_cast<size_t>(i)];
      PyObject* item = PyBytes_FromStringAndSize(value.data(),
                                                 static_cast<Py_ssize_t>(value.size()));
      if (item == nullptr) {
        Py_DECREF(list);
        return nullptr;
      }
      PyList_SetItem(list, i, item);
    }
    return list;
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_store_put_data(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  PyObject* keys_obj = nullptr;
  PyObject* values_obj = nullptr;
  if (!PyArg_ParseTuple(args, "OOO", &capsule, &keys_obj, &values_obj)) {
    return nullptr;
  }

  auto* store = capsule_to_ptr<vdb::KVStore>(capsule, kStoreCapsuleName);
  if (store == nullptr) {
    return nullptr;
  }

  std::vector<std::string> keys;
  std::vector<std::string> values;
  if (!parse_string_list(keys_obj, &keys) || !parse_binary_list(values_obj, &values)) {
    return nullptr;
  }
  if (keys.size() != values.size()) {
    raise_value_error("keys and values must have the same length");
    return nullptr;
  }

  try {
    const int result = call_without_gil([&]() { return store->put_data(keys, values); });
    return PyLong_FromLong(result);
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_store_delete_data(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  PyObject* keys_obj = nullptr;
  if (!PyArg_ParseTuple(args, "OO", &capsule, &keys_obj)) {
    return nullptr;
  }

  auto* store = capsule_to_ptr<vdb::KVStore>(capsule, kStoreCapsuleName);
  if (store == nullptr) {
    return nullptr;
  }

  std::vector<std::string> keys;
  if (!parse_string_list(keys_obj, &keys)) {
    return nullptr;
  }

  try {
    const int result = call_without_gil([&]() { return store->delete_data(keys); });
    return PyLong_FromLong(result);
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_store_clear_data(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  if (!PyArg_ParseTuple(args, "O", &capsule)) {
    return nullptr;
  }

  auto* store = capsule_to_ptr<vdb::KVStore>(capsule, kStoreCapsuleName);
  if (store == nullptr) {
    return nullptr;
  }

  try {
    const int result = call_without_gil([&]() { return store->clear_data(); });
    return PyLong_FromLong(result);
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyObject* py_store_seek_range(PyObject*, PyObject* args) {
  PyObject* capsule = nullptr;
  const char* start_key = nullptr;
  const char* end_key = nullptr;
  if (!PyArg_ParseTuple(args, "Oss", &capsule, &start_key, &end_key)) {
    return nullptr;
  }

  auto* store = capsule_to_ptr<vdb::KVStore>(capsule, kStoreCapsuleName);
  if (store == nullptr) {
    return nullptr;
  }

  try {
    const auto items = call_without_gil([&]() { return store->seek_range(start_key, end_key); });
    PyObject* list = PyList_New(static_cast<Py_ssize_t>(items.size()));
    if (list == nullptr) {
      return nullptr;
    }
    for (Py_ssize_t i = 0; i < static_cast<Py_ssize_t>(items.size()); ++i) {
      const auto& item = items[static_cast<size_t>(i)];
      PyObject* tuple = PyTuple_New(2);
      PyObject* key = PyUnicode_FromStringAndSize(item.first.data(),
                                                  static_cast<Py_ssize_t>(item.first.size()));
      PyObject* value = PyBytes_FromStringAndSize(item.second.data(),
                                                  static_cast<Py_ssize_t>(item.second.size()));
      if (tuple == nullptr || key == nullptr || value == nullptr) {
        Py_XDECREF(tuple);
        Py_XDECREF(key);
        Py_XDECREF(value);
        Py_DECREF(list);
        return nullptr;
      }
      PyTuple_SetItem(tuple, 0, key);
      PyTuple_SetItem(tuple, 1, value);
      PyList_SetItem(list, i, tuple);
    }
    return list;
  } catch (const std::exception& exc) {
    raise_runtime_error(exc.what());
    return nullptr;
  }
}

PyMethodDef kModuleMethods[] = {
    {"_new_schema", py_new_schema, METH_VARARGS, "Create a schema handle."},
    {"_schema_get_total_byte_length", py_schema_get_total_byte_length, METH_VARARGS,
     "Read total schema byte length."},
    {"_new_bytes_row", py_new_bytes_row, METH_VARARGS, "Create a BytesRow handle."},
    {"_bytes_row_serialize", py_bytes_row_serialize, METH_VARARGS,
     "Serialize a row using the native BytesRow implementation."},
    {"_bytes_row_serialize_batch", py_bytes_row_serialize_batch, METH_VARARGS,
     "Serialize a batch of rows using the native BytesRow implementation."},
    {"_bytes_row_deserialize", py_bytes_row_deserialize, METH_VARARGS,
     "Deserialize a row using the native BytesRow implementation."},
    {"_bytes_row_deserialize_field", py_bytes_row_deserialize_field, METH_VARARGS,
     "Deserialize a single field using the native BytesRow implementation."},
    {"_init_logging", reinterpret_cast<PyCFunction>(py_init_logging),
     METH_VARARGS | METH_KEYWORDS, "Initialize vectordb logging."},
    {"_new_index_engine", py_new_index_engine, METH_VARARGS, "Create an index engine handle."},
    {"_index_engine_add_data", py_index_engine_add_data, METH_VARARGS,
     "Add data to the index engine."},
    {"_index_engine_delete_data", py_index_engine_delete_data, METH_VARARGS,
     "Delete data from the index engine."},
    {"_index_engine_search", py_index_engine_search, METH_VARARGS,
     "Search the index engine."},
    {"_index_engine_search_batch", py_index_engine_search_batch, METH_VARARGS,
     "Batch search the index engine with multiple queries."},
    {"_index_engine_dump", py_index_engine_dump, METH_VARARGS, "Dump index state to disk."},
    {"_index_engine_get_state", py_index_engine_get_state, METH_VARARGS,
     "Read index engine state."},
    {"_new_persist_store", py_new_persist_store, METH_VARARGS, "Create a persistent store."},
    {"_new_volatile_store", py_new_volatile_store, METH_NOARGS, "Create a volatile store."},
    {"_store_exec_op", py_store_exec_op, METH_VARARGS, "Execute store operations."},
    {"_store_get_data", py_store_get_data, METH_VARARGS, "Read values from the store."},
    {"_store_put_data", py_store_put_data, METH_VARARGS, "Write values to the store."},
    {"_store_delete_data", py_store_delete_data, METH_VARARGS, "Delete keys from the store."},
    {"_store_clear_data", py_store_clear_data, METH_VARARGS, "Clear the store."},
    {"_store_seek_range", py_store_seek_range, METH_VARARGS,
     "Read a key range from the store."},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef kModuleDef = {
    PyModuleDef_HEAD_INIT,
    "_ov_engine_backend",
    "OpenViking abi3 vectordb backend.",
    -1,
    kModuleMethods,
};

}  // namespace

#ifndef OV_PY_MODULE_NAME
#define OV_PY_MODULE_NAME _native
#endif

#define OV_CONCAT_IMPL(a, b) a##b
#define OV_CONCAT(a, b) OV_CONCAT_IMPL(a, b)

PyMODINIT_FUNC OV_CONCAT(PyInit_, OV_PY_MODULE_NAME)(void) {
  PyObject* module = PyModule_Create(&kModuleDef);
  if (module == nullptr) {
    return nullptr;
  }

  if (PyModule_AddStringConstant(module, "_ENGINE_BACKEND_API", "abi3-v1") < 0) {
    Py_DECREF(module);
    return nullptr;
  }

  return module;
}
