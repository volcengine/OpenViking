// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/complex.h>
#include <chrono>
#include <iostream>
#include "index/index_engine.h"
#include "store/persist_store.h"
#include "store/volatile_store.h"
#include "common/log_utils.h"
#include "store/bytes_row.h"
#include "py_accessors.h"

namespace py = pybind11;
namespace vdb = vectordb;

PYBIND11_MODULE(engine, m) {
  m.def("init_logging", &vdb::init_logging, "Initialize logging");

  py::enum_<vdb::FieldType>(m, "FieldType")
      .value("int64", vdb::FieldType::INT64)
      .value("uint64", vdb::FieldType::UINT64)
      .value("float32", vdb::FieldType::FLOAT32)
      .value("string", vdb::FieldType::STRING)
      .value("binary", vdb::FieldType::BINARY)
      .value("boolean", vdb::FieldType::BOOLEAN)
      .value("list_int64", vdb::FieldType::LIST_INT64)
      .value("list_string", vdb::FieldType::LIST_STRING)
      .value("list_float32", vdb::FieldType::LIST_FLOAT32);

  py::class_<vdb::Schema, std::shared_ptr<vdb::Schema>>(m, "Schema")
      .def(py::init([](const py::list& fields_py) {
        std::vector<vdb::FieldDef> fields;
        for (const auto& item : fields_py) {
          py::dict d = item.cast<py::dict>();
          vdb::FieldDef fd;
          fd.name = d["name"].cast<std::string>();
          fd.data_type = d["data_type"].cast<vdb::FieldType>();
          fd.id = d["id"].cast<int>();
          if (d.contains("default_value")) {
            try {
              switch (fd.data_type) {
                case vdb::FieldType::INT64:
                  fd.default_value = d["default_value"].cast<int64_t>();
                  break;
                case vdb::FieldType::UINT64:
                  fd.default_value = d["default_value"].cast<uint64_t>();
                  break;
                case vdb::FieldType::FLOAT32:
                  fd.default_value = d["default_value"].cast<float>();
                  break;
                case vdb::FieldType::BOOLEAN:
                  fd.default_value = d["default_value"].cast<bool>();
                  break;
                case vdb::FieldType::STRING:
                  fd.default_value = d["default_value"].cast<std::string>();
                  break;
                case vdb::FieldType::BINARY:
                  fd.default_value = d["default_value"].cast<std::string>();
                  break;
                case vdb::FieldType::LIST_INT64:
                  fd.default_value =
                      d["default_value"].cast<std::vector<int64_t>>();
                  break;
                case vdb::FieldType::LIST_FLOAT32:
                  fd.default_value =
                      d["default_value"].cast<std::vector<float>>();
                  break;
                case vdb::FieldType::LIST_STRING:
                  fd.default_value =
                      d["default_value"].cast<std::vector<std::string>>();
                  break;
              }
            } catch (...) {
              fd.default_value = std::monostate{};
            }
          } else {
            fd.default_value = std::monostate{};
          }
          fields.push_back(fd);
        }
        return std::make_shared<vdb::Schema>(fields);
      }))
      .def("get_total_byte_length", &vdb::Schema::get_total_byte_length);

  py::class_<vdb::BytesRow>(m, "BytesRow")
      .def(py::init<std::shared_ptr<vdb::Schema>>())
      .def("serialize",
           [](vdb::BytesRow& self, const py::dict& row_data) {
             PyDictAccessor accessor(self.get_schema());
             std::string serialized =
                 self.serialize_template(row_data, accessor);
             return py::bytes(serialized);
           })
      .def("serialize_batch",
           [](vdb::BytesRow& self, const py::list& objects) {
             py::list results;
             const auto& schema = self.get_schema();

             PyDictAccessor dict_accessor(schema);
             PyObjectAccessor obj_accessor(schema);

             for (const auto& obj : objects) {
               std::string serialized;
               if (py::isinstance<py::dict>(obj)) {
                 serialized = self.serialize_template(obj.cast<py::dict>(),
                                                      dict_accessor);
               } else {
                 serialized = self.serialize_template(obj, obj_accessor);
               }
               results.append(py::bytes(serialized));
             }
             return results;
           })
      .def("deserialize",
           [](vdb::BytesRow& self, const std::string& data) {
             py::dict res_dict;
             const auto& schema = self.get_schema();

             const auto& field_order = schema.get_field_order();
             for (const auto& meta : field_order) {
               vdb::Value val = self.deserialize_field(data, meta.name);

               if (std::holds_alternative<std::monostate>(val))
                 continue;

               if (meta.data_type == vdb::FieldType::BINARY) {
                 if (std::holds_alternative<std::string>(val)) {
                   res_dict[meta.name.c_str()] =
                       py::bytes(std::get<std::string>(val));
                   continue;
                 }
               }
               res_dict[meta.name.c_str()] = value_to_py(val);
             }
             return res_dict;
           })
      .def("deserialize_field",
           [](vdb::BytesRow& self, const std::string& data,
              const std::string& field_name) -> py::object {
             vdb::Value val = self.deserialize_field(data, field_name);
             const auto& schema = self.get_schema();
             const auto* meta = schema.get_field_meta(field_name);

             if (meta && meta->data_type == vdb::FieldType::BINARY) {
               if (std::holds_alternative<std::string>(val)) {
                 const auto& s = std::get<std::string>(val);
                 return py::bytes(s);
               }
             }
             return value_to_py(val);
           });

  py::class_<vdb::AddDataRequest>(m, "AddDataRequest")
      .def(py::init<>())
      .def_readwrite("label", &vdb::AddDataRequest::label)
      .def_readwrite("vector", &vdb::AddDataRequest::vector)
      .def_readwrite("sparse_raw_terms", &vdb::AddDataRequest::sparse_raw_terms)
      .def_readwrite("sparse_values", &vdb::AddDataRequest::sparse_values)
      .def_readwrite("fields_str", &vdb::AddDataRequest::fields_str)
      .def_readwrite("old_fields_str", &vdb::AddDataRequest::old_fields_str)
      .def("__repr__", [](const vdb::AddDataRequest& p) {
        return "<AddDataRequest label=" + std::to_string(p.label) +
               ", vector=" + std::to_string(p.vector.size()) + ">";
      });

  py::class_<vdb::DeleteDataRequest>(m, "DeleteDataRequest")
      .def(py::init<>())
      .def_readwrite("label", &vdb::DeleteDataRequest::label)
      .def_readwrite("old_fields_str", &vdb::DeleteDataRequest::old_fields_str)
      .def("__repr__", [](const vdb::DeleteDataRequest& p) {
        return "<DeleteDataRequest label=" + std::to_string(p.label) +
               ", old_fields_str=" + p.old_fields_str + ">";
      });

  py::class_<vdb::SearchRequest>(m, "SearchRequest")
      .def(py::init<>())
      .def_readwrite("query", &vdb::SearchRequest::query)
      .def_readwrite("sparse_raw_terms", &vdb::SearchRequest::sparse_raw_terms)
      .def_readwrite("sparse_values", &vdb::SearchRequest::sparse_values)
      .def_readwrite("topk", &vdb::SearchRequest::topk)
      .def_readwrite("dsl", &vdb::SearchRequest::dsl)
      .def("__repr__", [](const vdb::SearchRequest& p) {
        return "<SearchRequest query=" + std::to_string(p.query.size()) +
               ", topk=" + std::to_string(p.topk) + ">";
      });

  py::class_<vdb::SearchResult>(m, "SearchResult")
      .def(py::init<>())
      .def_readwrite("result_num", &vdb::SearchResult::result_num)
      .def_readwrite("labels", &vdb::SearchResult::labels)
      .def_readwrite("scores", &vdb::SearchResult::scores)
      .def_readwrite("extra_json", &vdb::SearchResult::extra_json)
      .def("__repr__", [](const vdb::SearchResult& p) {
        return "<SearchResult result_num=" + std::to_string(p.result_num) +
               ", labels=" + std::to_string(p.labels.size()) +
               ", scores=" + std::to_string(p.scores.size()) + ">";
      });

  py::class_<vdb::FetchDataResult>(m, "FetchDatahResult")
      .def(py::init<>())
      .def_readwrite("embedding", &vdb::FetchDataResult::embedding)
      .def("__repr__", [](const vdb::FetchDataResult& p) {
        return "<FetchDataResult embedding=" +
               std::to_string(p.embedding.size()) + ">";
      });

  py::class_<vdb::StateResult>(m, "StateResult")
      .def(py::init<>())
      .def_readwrite("update_timestamp", &vdb::StateResult::update_timestamp)
      .def_readwrite("element_count", &vdb::StateResult::element_count)
      .def("__repr__", [](const vdb::StateResult& p) {
        return "<StateResult update_timestamp=" +
               std::to_string(p.update_timestamp) +
               ", element_count=" + std::to_string(p.element_count) + ">";
      });

  py::class_<vdb::IndexEngine>(m, "IndexEngine")
      .def(py::init<const std::string&>())
      .def(
          "add_data",
          [](vdb::IndexEngine& self,
             const std::vector<vdb::AddDataRequest>& data_list) {
            pybind11::gil_scoped_release release;
            return self.add_data(data_list);
          },
          "add data to index")
      .def(
          "delete_data",
          [](vdb::IndexEngine& self,
             const std::vector<vdb::DeleteDataRequest>& data_list) {
            pybind11::gil_scoped_release release;
            return self.delete_data(data_list);
          },
          "delete data from index")
      .def(
          "search",
          [](vdb::IndexEngine& self, const vdb::SearchRequest& req) {
            pybind11::gil_scoped_release release;
            return self.search(req);
          },
          "search")
      .def(
          "dump",
          [](vdb::IndexEngine& self, const std::string& dir) {
            pybind11::gil_scoped_release release;
            return self.dump(dir);
          },
          "dump index")
      .def("get_state", &vdb::IndexEngine::get_state, "get index state");

  py::class_<vdb::VolatileStore>(m, "VolatileStore")
      .def(py::init<>())
      .def("exec_op", &vdb::VolatileStore::exec_op, "exec op")
      .def(
          "get_data",
          [](vdb::VolatileStore& self, const std::vector<std::string>& keys) {
            std::vector<std::string> cxx_bin_list = self.get_data(keys);

            py::list py_bytes_list;
            for (auto& cxx_bin : cxx_bin_list) {
              py_bytes_list.append(py::bytes(cxx_bin.data(), cxx_bin.size()));
            }
            return py_bytes_list;
          },
          "get data")
      .def("delete_data", &vdb::VolatileStore::delete_data, "delete data")
      .def("put_data", &vdb::VolatileStore::put_data, "put data")
      .def("clear_data", &vdb::VolatileStore::clear_data, "clear data")
      .def(
          "seek_range",
          [](vdb::VolatileStore& self, const std::string& start_key,
             const std::string& end_key) {
            std::vector<std::pair<std::string, std::string>> cxx_kv_list =
                self.seek_range(start_key, end_key);
            py::list py_kv_list;
            for (const auto& cxx_pair : cxx_kv_list) {
              py::tuple py_pair(2);
              py_pair[0] = cxx_pair.first;
              py_pair[1] =
                  py::bytes(cxx_pair.second.data(), cxx_pair.second.size());
              py_kv_list.append(py_pair);
            }
            return py_kv_list;
          },
          "seek range");

  py::class_<vdb::PersistStore>(m, "PersistStore")
      .def(py::init<const std::string&>())
      .def(
          "exec_op",
          [](vdb::PersistStore& self, const std::vector<vdb::StorageOp>& ops) {
            pybind11::gil_scoped_release release;
            return self.exec_op(ops);
          },
          "exec op")
      .def(
          "get_data",
          [](vdb::PersistStore& self, const std::vector<std::string>& keys) {
            std::vector<std::string> cxx_bin_list;
            {
              pybind11::gil_scoped_release release;
              cxx_bin_list = self.get_data(keys);
            }

            py::list py_bytes_list;
            for (auto& cxx_bin : cxx_bin_list) {
              py_bytes_list.append(py::bytes(cxx_bin.data(), cxx_bin.size()));
            }
            return py_bytes_list;
          },
          "get data")
      .def("delete_data", &vdb::PersistStore::delete_data, "delete data")
      .def("put_data", &vdb::PersistStore::put_data, "put data")
      .def("clear_data", &vdb::PersistStore::clear_data, "clear data")
      .def(
          "seek_range",
          [](vdb::PersistStore& self, const std::string& start_key,
             const std::string& end_key) {
            std::vector<std::pair<std::string, std::string>> cxx_kv_list =
                self.seek_range(start_key, end_key);
            py::list py_kv_list;

            for (const auto& cxx_pair : cxx_kv_list) {
              py::tuple py_pair(2);
              py_pair[0] = cxx_pair.first;
              py_pair[1] =
                  py::bytes(cxx_pair.second.data(), cxx_pair.second.size());
              py_kv_list.append(py_pair);
            }
            return py_kv_list;
          },
          "seek range");

  py::enum_<vdb::StorageOp::OpType>(m, "StorageOpType")
      .value("PUT", vdb::StorageOp::OpType::PUT_OP)
      .value("DELETE", vdb::StorageOp::OpType::DELETE_OP);

  py::class_<vdb::StorageOp>(m, "StorageOp")
      .def(py::init<>())
      .def_readwrite("type", &vdb::StorageOp::type)
      .def_readwrite("key", &vdb::StorageOp::key)
      .def_readwrite("value", &vdb::StorageOp::value);
}
