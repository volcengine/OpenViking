// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#include "index/detail/index_manager_impl.h"
#include <stdexcept>
#include <memory>
#include <chrono>
#include <thread>
#include "spdlog/spdlog.h"
#include "common/ann_utils.h"
#include "index/detail/scalar/filter/op_base.h"
#include "index/detail/scalar/filter/filter_ops.h"
#include "index/detail/scalar/filter/sort_ops.h"
#include "index/detail/scalar/bitmap_holder/bitmap.h"

namespace vectordb {

const std::string kMetaFile = "manager_meta.json";
const std::string kVectorIndexDir = "vector_index";
const std::string kScalarIndexDir = "scalar_index";

IndexManagerImpl::IndexManagerImpl(const std::string& path_or_json) {
  int ret = 0;
  std::filesystem::path dir(path_or_json);
  std::error_code ec;
  if (std::filesystem::exists(dir, ec)) {
    load_from_path(dir);
    return;
  }

  JsonDoc json;
  json.Parse(path_or_json.c_str());
  if (!json.HasParseError()) {
    init_from_json(json);
    return;
  }
  return;
}

void IndexManagerImpl::init_from_json(const JsonDoc& json) {
  manager_meta_ = std::make_shared<ManagerMeta>();
  if (manager_meta_->init_from_json(json) != 0) {
    SPDLOG_ERROR(
        "IndexManagerImpl::init_from_json manager_meta_ init_from_json failed");
    throw std::runtime_error(
        "IndexManagerImpl::init_from_json manager_meta_ init_from_json failed");
  }
  SPDLOG_DEBUG("IndexManagerImpl::init_from_json vector_index_type: {}",
               manager_meta_->vector_index_type);

  if (manager_meta_->vector_index_type == "flat") {
    auto bf_meta = std::dynamic_pointer_cast<BruteForceMeta>(
        manager_meta_->vector_index_meta);
    vector_index_ = std::make_shared<BruteForceIndex>(bf_meta);
  } else {
    SPDLOG_ERROR("IndexManagerImpl::init_from_json not support index_type={}",
                 manager_meta_->vector_index_type);
    throw std::runtime_error(
        "IndexManagerImpl::init_from_json not support index_type=" +
        manager_meta_->vector_index_type);
  }
  if (manager_meta_->scalar_index_meta) {
    scalar_index_ =
        std::make_shared<ScalarIndex>(manager_meta_->scalar_index_meta);
    register_label_offset_converter_();
  } else {
    SPDLOG_WARN(
        "IndexManagerImpl::init_from_json manager_meta_ scalar_index_meta is "
        "null");
  }
  return;
}

void IndexManagerImpl::load_from_path(const std::filesystem::path& dir) {
  auto meta_path = dir / kMetaFile;
  manager_meta_ = std::make_shared<ManagerMeta>();
  int ret = 0;
  ret = manager_meta_->init_from_file(meta_path);
  if (ret != 0) {
    SPDLOG_ERROR("IndexManagerImpl::load meta file failed, ret={}", ret);
    throw std::runtime_error("IndexManagerImpl::load meta file failed, ret=" +
                             std::to_string(ret));
  }

  if (manager_meta_->vector_index_type == "flat") {
    auto bf_meta = std::dynamic_pointer_cast<BruteForceMeta>(
        manager_meta_->vector_index_meta);
    vector_index_ = std::make_shared<BruteForceIndex>(bf_meta);
  } else {
    SPDLOG_ERROR("IndexLoader::load not support index_type={}",
                 manager_meta_->vector_index_type);
    throw std::runtime_error("IndexManagerImpl::not support index_type=" +
                             manager_meta_->vector_index_type);
  }
  auto vector_index_dir = dir / kVectorIndexDir;
  ret = vector_index_->load(vector_index_dir);
  if (ret != 0) {
    SPDLOG_ERROR("IndexManagerImpl::load index failed, ret={}", ret);
    throw std::runtime_error("IndexManagerImpl::load  index failed, ret=" +
                             std::to_string(ret));
  }

  auto scalar_index_dir = dir / kScalarIndexDir;
  scalar_index_ = std::make_shared<ScalarIndex>(
      manager_meta_->scalar_index_meta, scalar_index_dir);
  register_label_offset_converter_();
  SPDLOG_DEBUG("IndexManagerImpl::load_from_path success, path: {}",
               dir.string());
}

void IndexManagerImpl::register_label_offset_converter_() {
  scalar_index_->get_field_sets()->register_label_offset_converter(
      [this](const std::vector<uint64_t>& labels,
             std::vector<uint32_t>& offsets) -> bool {
        try {
          offsets.clear();
          offsets.reserve(labels.size());
          for (auto label : labels) {
            if (!vector_index_) {
              SPDLOG_ERROR("label_offset_converter vector_index_ is null");
              return false;
            }
            int offset = vector_index_->get_offset_by_label(label);
            if (offset >= 0) {
              offsets.push_back(static_cast<uint32_t>(offset));
            }
          }
          return true;
        } catch (const std::exception& e) {
          SPDLOG_ERROR("label_offset_converter exception: {}", e.what());
          return false;
        } catch (...) {
          SPDLOG_ERROR("label_offset_converter unknown exception");
          return false;
        }
      });
}

int parse_dsl_query(const std::string& dsl_filter_query_str,
                    SearchContext& ctx) {
  if (dsl_filter_query_str.empty()) {
    return 0;
  }
  JsonDoc dsl_filter_query;

  dsl_filter_query.Parse(dsl_filter_query_str.c_str());

  bool has_filter = false;
  bool has_sorter = false;
  if (parse_and_precheck_op_parts(dsl_filter_query, has_filter, has_sorter) <
      0) {
    return -1;
  }
  if (has_filter) {
    ctx.filter_op = parse_filter_json_doc_outter(dsl_filter_query);
  }
  if (has_sorter) {
    ctx.sorter_op = parse_sorter_json_doc_outter(dsl_filter_query);
  }
  return 0;
}

int IndexManagerImpl::search(const SearchRequest& req, SearchResult& result) {
  auto start = std::chrono::high_resolution_clock::now();
  const auto& dsl_filter_query_str = req.dsl;

  SearchContext ctx;
  if (int ret = parse_dsl_query(dsl_filter_query_str, ctx); ret != 0) {
    SPDLOG_ERROR("IndexManagerImpl::search [{}] scalar index search fail",
                 dsl_filter_query_str);
    return ret;
  }

  std::shared_lock<std::shared_mutex> lock(rw_mutex_);

  BitmapPtr bitmap = nullptr;
  if (ctx.filter_op) {
    bitmap = calculate_filter_bitmap(ctx, dsl_filter_query_str);
    if (!bitmap) {
      SPDLOG_DEBUG(
          "IndexManagerImpl::search calculate_filter_bitmap returned null");
      return -1;
    }
  }

  int ret = 0;
  if (ctx.sorter_op) {
    ret = handle_sorter_query(ctx, bitmap, result, dsl_filter_query_str);
  } else if (!req.query.empty()) {
    ret = perform_vector_recall(req, ctx, bitmap, result);
  }

  if (ret == 0) {
    auto end = std::chrono::high_resolution_clock::now();
    auto duration =
        std::chrono::duration_cast<std::chrono::microseconds>(end - start)
            .count();
    SPDLOG_DEBUG(
        "IndexManagerImpl::search finish, dsl: {}, query size: {}, topk: {}, "
        "result size: {}, cost: {}us",
        req.dsl, req.query.size(), req.topk, result.labels.size(), duration);
  }

  return ret;
}

BitmapPtr IndexManagerImpl::calculate_filter_bitmap(const SearchContext& ctx,
                                                    const std::string& dsl) {
  auto bitmap = ctx.filter_op->calc_bitmap(scalar_index_->get_field_sets(),
                                           nullptr, ctx.filter_op->op_name());
  if (!bitmap) {
    SPDLOG_DEBUG("ScalarIndex::search [{}] calc_bitmap fail", dsl);
  }
  return bitmap;
}

int IndexManagerImpl::handle_sorter_query(const SearchContext& ctx,
                                          const BitmapPtr& bitmap,
                                          SearchResult& result,
                                          const std::string& dsl) {
  if (ctx.sorter_op->op_name() == "count" && !ctx.filter_op) {
    uint64_t valid_data_num = vector_index_->get_data_num();

    JsonDoc json_result;
    json_result.SetObject();
    JsonDoc::AllocatorType& allocator = json_result.GetAllocator();

    JsonValue key;
    JsonValue value;
    key.SetString("__total_count__", sizeof("__total_count__") - 1, allocator);
    value.SetInt64(static_cast<int64_t>(valid_data_num));
    json_result.AddMember(key, value, allocator);

    result.extra_json = json_stringify(json_result);

    SPDLOG_DEBUG(
        "Count without filter: returning {} from vector index, dsl: {}",
        valid_data_num, dsl);
    return 0;
  }

  auto sorter_res =
      ctx.sorter_op->calc_topk_result(scalar_index_->get_field_sets(), bitmap);
  if (sorter_res) {
    for (size_t i = 0; i < sorter_res->offsets.size(); ++i) {
      auto label = vector_index_->get_label_by_offset(sorter_res->offsets[i]);
      sorter_res->labels_u64.push_back(label);
    }
    std::swap(result.scores, sorter_res->scores);
    std::swap(result.labels, sorter_res->labels_u64);
    if (sorter_res->dsl_op_extra_json) {
      result.extra_json = json_stringify(*sorter_res->dsl_op_extra_json);
    }
  }
  return 0;
}

int IndexManagerImpl::perform_vector_recall(const SearchRequest& req,
                                            SearchContext& ctx,
                                            const BitmapPtr& bitmap,
                                            SearchResult& result) {
  VectorRecallRequest recall_request{
      .dense_vector = req.query.data(),
      .topk = req.topk,
      .bitmap = bitmap.get(),
      .sparse_terms =
          req.sparse_raw_terms.empty() ? nullptr : &req.sparse_raw_terms,
      .sparse_values =
          req.sparse_values.empty() ? nullptr : &req.sparse_values};

  VectorRecallResult recall_result;
  int ret = vector_index_->recall(recall_request, recall_result);
  if (ret != 0) {
    SPDLOG_ERROR("IndexManagerImpl::search vector recall failed, ret={}", ret);
    return ret;
  }

  std::swap(result.labels, recall_result.labels);
  std::swap(result.scores, recall_result.scores);
  return 0;
}

int IndexManagerImpl::add_data(const std::vector<AddDataRequest>& data_list) {
  auto start = std::chrono::high_resolution_clock::now();
  std::vector<FieldsDict> parsed_fields_list(data_list.size());
  std::vector<FieldsDict> parsed_old_fields_list(data_list.size());

  for (size_t i = 0; i < data_list.size(); ++i) {
    if (!data_list[i].fields_str.empty()) {
      parsed_fields_list[i].parse_from_json(data_list[i].fields_str);
    }
    if (!data_list[i].old_fields_str.empty()) {
      parsed_old_fields_list[i].parse_from_json(data_list[i].old_fields_str);
    }
  }

  bool has_update = false;
  std::unique_lock<std::shared_mutex> lock(rw_mutex_);
  for (size_t i = 0; i < data_list.size(); ++i) {
    const auto& data = data_list[i];
    FloatValSparseDatapointLowLevel sparse_datapoint(&data.sparse_raw_terms,
                                                     &data.sparse_values);
    vector_index_->stream_add_data(data.label, data.vector.data(),
                                   &sparse_datapoint);
    int offset = vector_index_->get_offset_by_label(data.label);
    if (offset < 0) {
      SPDLOG_WARN("IndexManagerImpl::add_data label={} not found", data.label);
      continue;
    } else {
      has_update = true;
    }

    scalar_index_->add_row_data(offset, parsed_fields_list[i],
                                parsed_old_fields_list[i]);
  }
  if (has_update) {
    auto duration = std::chrono::system_clock::now().time_since_epoch();
    manager_meta_->update_timestamp =
        std::chrono::duration_cast<std::chrono::nanoseconds>(duration).count();
  }

  auto end = std::chrono::high_resolution_clock::now();
  auto duration_us =
      std::chrono::duration_cast<std::chrono::microseconds>(end - start)
          .count();
  SPDLOG_DEBUG("IndexManagerImpl::add_data finish, batch size: {}, cost: {}us",
               data_list.size(), duration_us);

  return 0;
}

int IndexManagerImpl::delete_data(
    const std::vector<DeleteDataRequest>& data_list) {
  auto start = std::chrono::high_resolution_clock::now();
  std::vector<FieldsDict> parsed_old_fields_list(data_list.size());
  for (size_t i = 0; i < data_list.size(); ++i) {
    if (!data_list[i].old_fields_str.empty()) {
      parsed_old_fields_list[i].parse_from_json(data_list[i].old_fields_str);
    }
  }

  bool has_update = false;
  std::unique_lock<std::shared_mutex> lock(rw_mutex_);
  for (size_t i = 0; i < data_list.size(); ++i) {
    const auto& data = data_list[i];
    int offset = vector_index_->get_offset_by_label(data.label);
    if (offset < 0) {
      SPDLOG_DEBUG("IndexManagerImpl::delete_data label={} not found",
                  data.label);
      continue;
    } else {
      has_update = true;
    }

    scalar_index_->delete_row_data(offset, parsed_old_fields_list[i]);
    vector_index_->stream_delete_data(data.label);
  }
  if (has_update) {
    auto duration = std::chrono::system_clock::now().time_since_epoch();
    manager_meta_->update_timestamp =
        std::chrono::duration_cast<std::chrono::nanoseconds>(duration).count();
  }
  auto end = std::chrono::high_resolution_clock::now();
  auto duration_us =
      std::chrono::duration_cast<std::chrono::microseconds>(end - start)
          .count();
  SPDLOG_DEBUG(
      "IndexManagerImpl::delete_data finish, batch size: {}, cost: {}us",
      data_list.size(), duration_us);
  return 0;
}

int64_t IndexManagerImpl::dump(const std::string& dir) {
  std::filesystem::path dir_path(dir);
  std::shared_lock<std::shared_mutex> lock(rw_mutex_);
  auto start = std::chrono::high_resolution_clock::now();
  auto scalar_index_dir = dir_path / kScalarIndexDir;
  std::error_code ec;
  std::filesystem::create_directories(scalar_index_dir, ec);
  if (ec) {
    SPDLOG_ERROR(
        "IndexManagerImpl::dump create_directories failed, path={}, ec={}",
        scalar_index_dir.string(), ec.message());
    throw std::runtime_error(
        "IndexManagerImpl::dump create_directories failed, path=" +
        scalar_index_dir.string());
  }
  scalar_index_->dump(scalar_index_dir);

  auto vector_index_dir = dir_path / kVectorIndexDir;
  std::filesystem::create_directories(vector_index_dir, ec);
  if (ec) {
    SPDLOG_ERROR(
        "IndexManagerImpl::dump create_directories failed, path={}, ec={}",
        vector_index_dir.string(), ec.message());
    throw std::runtime_error(
        "IndexManagerImpl::dump create_directories failed, path=" +
        vector_index_dir.string());
  }
  vector_index_->dump(vector_index_dir);
  auto manager_meta_path = dir_path / kMetaFile;
  manager_meta_->save_to_file(manager_meta_path);

  auto end = std::chrono::high_resolution_clock::now();
  auto duration_total =
      std::chrono::duration_cast<std::chrono::microseconds>(end - start);
  SPDLOG_DEBUG("IndexManagerImpl::dump finish, path: {}, cost: {}us", dir,
               duration_total.count());

  return manager_meta_->update_timestamp;
}

int IndexManagerImpl::get_state(StateResult& state_result) {
  state_result.update_timestamp = manager_meta_->update_timestamp;
  return 0;
}

}  // namespace vectordb
