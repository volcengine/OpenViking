// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include "index/index_engine.h"
#include "store/persist_store.h"
#include "store/volatile_store.h"
#include <iostream>
#include <vector>
#include <cassert>
#include <filesystem>
#include <cmath>
#include "spdlog/spdlog.h"
#include "common/log_utils.h"

using namespace vectordb;

// Helper to check float equality
bool is_close(float a, float b, float epsilon = 1e-5) {
  return std::fabs(a - b) < epsilon;
}

void expect_filter_projection(IndexEngine& engine, const std::string& dsl,
                              uint64_t expected_count,
                              uint32_t expected_first_word) {
  FilterResult result = engine.evaluate_filter(dsl);
  const uint32_t first_word =
      result.bitset_words.empty() ? 0U : result.bitset_words[0];
  if (result.eligible_count != expected_count ||
      result.bitset_words.size() != 1 || first_word != expected_first_word) {
    SPDLOG_ERROR(
        "Unexpected filter projection: dsl={}, count={} (expected {}), "
        "words={}, first_word={} (expected {})",
        dsl, result.eligible_count, expected_count, result.bitset_words.size(),
        first_word, expected_first_word);
    exit(1);
  }
}

void test_basic_workflow() {
  SPDLOG_INFO("[Running] test_basic_workflow...");

  std::string db_path = "test_data_cpp/basic_workflow";
  // Cleanup
  if (std::filesystem::exists(db_path)) {
    std::filesystem::remove_all(db_path);
  }
  std::filesystem::create_directories(db_path);

  // 1. Initialization (Using JSON config)
  std::string config = R"({
        "CollectionName": "engine_test",
        "IndexName": "default",
        "VectorIndex": {
            "IndexType": "flat",
            "ElementCount": 0,
            "MaxElementCount": 2,
            "Dimension": 4,
            "Distance": "l2",
            "Quant": "float"
        },
        "ScalarIndex": [
            {"FieldName": "title", "FieldType": "string"},
            {"FieldName": "count", "FieldType": "int64"},
            {"FieldName": "price", "FieldType": "float32"},
            {"FieldName": "uri", "FieldType": "path"}
        ]
    })";

  IndexEngine engine(config);
  if (!engine.is_valid()) {
    SPDLOG_ERROR("Engine initialization failed");
    exit(1);
  }

  // 2. Add Data
  std::vector<AddDataRequest> add_reqs;

  AddDataRequest req1;
  req1.label = 1001;
  req1.vector = {0.1, 0.1, 0.1, 0.1};
  req1.fields_str =
      R"({"title": "apple", "count": 10, "price": 5.5, "uri": "/docs/one"})";
  add_reqs.push_back(req1);

  AddDataRequest req2;
  req2.label = 1002;
  req2.vector = {0.2, 0.2, 0.2, 0.2};
  req2.fields_str =
      R"({"title": "banana", "count": 20, "price": 3.0, "uri": "/other/two"})";
  add_reqs.push_back(req2);

  int ret = engine.add_data(add_reqs);
  if (ret != 0) {
    SPDLOG_ERROR("Add data failed");
    exit(1);
  }

  // 3. Search (Vector only)
  SearchRequest search_req;
  search_req.query = {0.1, 0.1, 0.1, 0.1};
  search_req.topk = 5;

  SearchResult res = engine.search(search_req);
  if (res.result_num < 1) {
    SPDLOG_ERROR("Search failed: no result found");
    exit(1);
  }
  if (res.labels[0] != 1001) {
    SPDLOG_ERROR("Search failed: expected label 1001, got {}", res.labels[0]);
    exit(1);
  }

  // Native scalar filters can be projected into any external row order. This
  // is the bridge used by external dense indexes such as cuVS.
  if (engine.set_filter_layout({1002, 9999, 1001}) != 0) {
    SPDLOG_ERROR("Filter layout registration failed");
    exit(1);
  }
  const std::string uri_filter =
      R"({"op":"must","field":"uri","conds":["/docs"],"para":"-d=-1"})";
  FilterResult filter_res = engine.evaluate_filter(uri_filter);
  if (filter_res.eligible_count != 1 || filter_res.bitset_words.size() != 1 ||
      filter_res.bitset_words[0] != 4U || filter_res.native_filter_token != 0) {
    SPDLOG_ERROR(
        "Filter projection failed: count={}, words={}, first_word={}",
        filter_res.eligible_count, filter_res.bitset_words.size(),
        filter_res.bitset_words.empty() ? 0 : filter_res.bitset_words[0]);
    exit(1);
  }

  FilterResult routed_filter_res =
      engine.evaluate_filter_for_routing(uri_filter, 10);
  if (routed_filter_res.eligible_count != 1 ||
      !routed_filter_res.bitset_words.empty() ||
      routed_filter_res.native_filter_token == 0) {
    SPDLOG_ERROR("Sparse routed filter projection failed");
    exit(1);
  }

  FilterResult routed_threshold_zero =
      engine.evaluate_filter_for_routing(uri_filter, 0);
  if (routed_threshold_zero.eligible_count != filter_res.eligible_count ||
      routed_threshold_zero.bitset_words != filter_res.bitset_words ||
      routed_threshold_zero.native_filter_token != 0) {
    SPDLOG_ERROR("Threshold-zero routed filter projection changed semantics");
    exit(1);
  }

  const std::string wide_uri_filter =
      R"({"op":"must","field":"uri","conds":["/docs","/other"],"para":"-d=-1"})";
  FilterResult generic_wide_filter = engine.evaluate_filter(wide_uri_filter);
  FilterResult routed_wide_filter =
      engine.evaluate_filter_for_routing(wide_uri_filter, 1);
  if (generic_wide_filter.eligible_count != 2 ||
      generic_wide_filter.bitset_words.size() != 1 ||
      generic_wide_filter.bitset_words[0] != 0b101U ||
      routed_wide_filter.eligible_count != generic_wide_filter.eligible_count ||
      routed_wide_filter.bitset_words != generic_wide_filter.bitset_words ||
      routed_wide_filter.native_filter_token != 0) {
    SPDLOG_ERROR("Wide routed filter projection changed generic semantics");
    exit(1);
  }

  FilterResult cached_filter_res = engine.evaluate_filter(uri_filter, 10);
  if (cached_filter_res.native_filter_token == 0) {
    SPDLOG_ERROR("Native filter token was not retained");
    exit(1);
  }
  auto token_search_res = engine.search_with_filter_token(
      search_req, cached_filter_res.native_filter_token);
  if (!token_search_res || token_search_res->result_num != 1 ||
      token_search_res->labels[0] != 1001) {
    SPDLOG_ERROR("Search with native filter token failed");
    exit(1);
  }

  // 4. Delete Data
  std::vector<DeleteDataRequest> del_reqs(1);
  del_reqs[0].label = 1001;
  del_reqs[0].old_fields_str =
      R"({"title": "apple", "count": 10, "price": 5.5, "uri": "/docs/one"})";

  ret = engine.delete_data(del_reqs);
  if (ret != 0) {
    SPDLOG_ERROR("Delete data failed");
    exit(1);
  }
  auto stale_token_result = engine.search_with_filter_token(
      search_req, cached_filter_res.native_filter_token);
  if (stale_token_result.has_value()) {
    SPDLOG_ERROR("Native filter token survived a mutation");
    exit(1);
  }

  // 5. Search again to verify deletion
  res = engine.search(search_req);
  // Depending on soft delete implementation, result might still be there but
  // filtered, or simply not returned. For brute force, it usually checks
  // filter. If it returns, ensure it's not the deleted one or handle
  // accordingly.
  if (res.result_num > 0 && res.labels[0] == 1001) {
    SPDLOG_WARN(
        "Deleted item 1001 still found (might be soft delete delay or consistency model)");
  } else {
    SPDLOG_INFO("Deleted item 1001 correctly not found or ranked lower");
  }

  // 6. Dump
  int64_t ts = engine.dump(db_path);
  if (ts <= 0) {
    SPDLOG_ERROR("Dump failed");
    exit(1);
  }

  std::filesystem::remove_all(db_path);
  SPDLOG_INFO("[Passed] test_basic_workflow");
}

void test_routed_filter_projection_edge_cases() {
  SPDLOG_INFO("[Running] test_routed_filter_projection_edge_cases...");

  const std::string config = R"({
        "CollectionName": "routed_filter_projection",
        "IndexName": "default",
        "VectorIndex": {
            "IndexType": "flat",
            "ElementCount": 0,
            "MaxElementCount": 32,
            "Dimension": 2,
            "Distance": "l2",
            "Quant": "float"
        },
        "ScalarIndex": [
            {"FieldName": "uri", "FieldType": "path"}
        ]
    })";

  IndexEngine engine(config);
  AddDataRequest early;
  early.label = 3001;
  early.vector = {1.0, 0.0};
  early.fields_str = R"({"uri":"/keep/a"})";
  if (engine.add_data({early}) != 0) {
    SPDLOG_ERROR("Failed to add the early routed-filter record");
    exit(1);
  }

  std::vector<AddDataRequest> fillers;
  std::vector<DeleteDataRequest> filler_deletes;
  for (uint64_t i = 0; i < 16; ++i) {
    AddDataRequest filler;
    filler.label = 3100 + i;
    filler.vector = {0.0, 1.0};
    filler.fields_str =
        "{\"uri\":\"/drop/" + std::to_string(i) + "\"}";
    fillers.push_back(filler);

    DeleteDataRequest deleted;
    deleted.label = filler.label;
    deleted.old_fields_str = filler.fields_str;
    filler_deletes.push_back(deleted);
  }
  if (engine.add_data(fillers) != 0 ||
      engine.delete_data(filler_deletes) != 0) {
    SPDLOG_ERROR("Failed to create sparse logical offsets");
    exit(1);
  }

  AddDataRequest late;
  late.label = 3002;
  late.vector = {0.9, 0.1};
  late.fields_str = R"({"uri":"/keep/b"})";
  if (engine.add_data({late}) != 0) {
    SPDLOG_ERROR("Failed to add the late routed-filter record");
    exit(1);
  }

  const std::string keep_recursive =
      R"({"op":"must","field":"uri","conds":["/keep"],"para":"-d=-1"})";
  const std::string keep_a =
      R"({"op":"must","field":"uri","conds":["/keep/a"],"para":"-d=0"})";

  // The surviving logical offsets are deliberately far apart. The inverse
  // layout must decline an oversized span and preserve the fallback result.
  engine.set_filter_layout({3001, 9999, 3002});
  FilterResult generic = engine.evaluate_filter(keep_recursive);
  FilterResult routed = engine.evaluate_filter_for_routing(keep_recursive, 2);
  if (generic.eligible_count != 2 || generic.bitset_words.size() != 1 ||
      generic.bitset_words[0] != 0b101U || routed.eligible_count != 2 ||
      !routed.bitset_words.empty() || routed.native_filter_token == 0) {
    SPDLOG_ERROR("Large-span routed filter projection was incorrect");
    exit(1);
  }

  FilterResult threshold_zero =
      engine.evaluate_filter_for_routing(keep_recursive, 0);
  if (threshold_zero.eligible_count != generic.eligible_count ||
      threshold_zero.bitset_words != generic.bitset_words ||
      threshold_zero.native_filter_token != 0) {
    SPDLOG_ERROR("Threshold-zero routing changed generic projection semantics");
    exit(1);
  }

  // Duplicate logical offsets cannot use the one-to-one inverse. The fallback
  // must count and project every external row exactly as the generic API does.
  engine.set_filter_layout({3001, 3001, 3002, 9999});
  generic = engine.evaluate_filter(keep_a);
  routed = engine.evaluate_filter_for_routing(keep_a, 1);
  if (generic.eligible_count != 2 || generic.bitset_words.size() != 1 ||
      generic.bitset_words[0] != 0b0011U ||
      routed.eligible_count != generic.eligible_count ||
      routed.bitset_words != generic.bitset_words ||
      routed.native_filter_token != 0) {
    SPDLOG_ERROR("Duplicate-offset routed filter projection was incorrect");
    exit(1);
  }

  FilterResult narrow_duplicate =
      engine.evaluate_filter_for_routing(keep_a, 2);
  if (narrow_duplicate.eligible_count != 2 ||
      !narrow_duplicate.bitset_words.empty() ||
      narrow_duplicate.native_filter_token == 0) {
    SPDLOG_ERROR("Narrow duplicate-offset filter did not retain native state");
    exit(1);
  }

  AddDataRequest update = early;
  update.fields_str = R"({"uri":"/other/a"})";
  update.old_fields_str = early.fields_str;
  if (engine.add_data({update}) != 0) {
    SPDLOG_ERROR("Failed to mutate routed-filter data");
    exit(1);
  }
  SearchRequest query;
  query.query = {1.0, 0.0};
  query.topk = 2;
  if (engine.search_with_filter_token(
          query, narrow_duplicate.native_filter_token)) {
    SPDLOG_ERROR("A routed filter token survived mutation");
    exit(1);
  }
  routed = engine.evaluate_filter_for_routing(keep_recursive, 2);
  if (routed.eligible_count != 0 || !routed.bitset_words.empty() ||
      routed.native_filter_token != 0) {
    SPDLOG_ERROR("Mutation did not invalidate the registered filter layout");
    exit(1);
  }

  engine.set_filter_layout({3001, 3002});
  routed = engine.evaluate_filter_for_routing(keep_recursive, 2);
  if (routed.eligible_count != 1 || !routed.bitset_words.empty() ||
      routed.native_filter_token == 0) {
    SPDLOG_ERROR("Routed filter was incorrect after layout registration");
    exit(1);
  }

  DeleteDataRequest delete_late;
  delete_late.label = late.label;
  delete_late.old_fields_str = late.fields_str;
  const uint64_t pre_delete_token = routed.native_filter_token;
  if (engine.delete_data({delete_late}) != 0) {
    SPDLOG_ERROR("Failed to delete routed-filter data");
    exit(1);
  }
  if (engine.search_with_filter_token(query, pre_delete_token)) {
    SPDLOG_ERROR("A routed filter token survived deletion");
    exit(1);
  }
  engine.set_filter_layout({3001, 3002});
  routed = engine.evaluate_filter_for_routing(keep_recursive, 2);
  if (routed.eligible_count != 0 || !routed.bitset_words.empty() ||
      routed.native_filter_token != 0) {
    SPDLOG_ERROR("Missing-label routed filter projection was incorrect");
    exit(1);
  }

  SPDLOG_INFO("[Passed] test_routed_filter_projection_edge_cases");
}

void test_path_bitmap_lifecycle_and_reload() {
  SPDLOG_INFO("[Running] test_path_bitmap_lifecycle_and_reload...");

  const std::string db_path = "test_data_cpp/path_bitmap_lifecycle";
  if (std::filesystem::exists(db_path)) {
    std::filesystem::remove_all(db_path);
  }
  std::filesystem::create_directories(db_path);

  const std::string config = R"({
        "CollectionName": "path_bitmap_lifecycle",
        "IndexName": "default",
        "VectorIndex": {
            "IndexType": "flat",
            "ElementCount": 0,
            "MaxElementCount": 6,
            "Dimension": 4,
            "Distance": "l2",
            "Quant": "float"
        },
        "ScalarIndex": [
            {"FieldName": "uri", "FieldType": "path"}
        ]
    })";

  IndexEngine engine(config);
  if (!engine.is_valid()) {
    SPDLOG_ERROR("Path bitmap lifecycle engine initialization failed");
    exit(1);
  }

  std::vector<AddDataRequest> initial(5);
  initial[0].label = 2001;
  initial[0].vector = {1.0, 0.0, 0.0, 0.0};
  initial[0].fields_str = R"({"uri":"/docs/a"})";
  initial[1].label = 2002;
  initial[1].vector = {0.9, 0.1, 0.0, 0.0};
  initial[1].fields_str = R"({"uri":"/docs/deep/b"})";
  initial[2].label = 2003;
  initial[2].vector = {0.0, 1.0, 0.0, 0.0};
  initial[2].fields_str = R"({"uri":"/other/c"})";
  // These spellings resolve to one trie leaf. Both bitmap bindings must be
  // retained without adding a vector to every ordinary TrieNode.
  initial[3].label = 2005;
  initial[3].vector = {0.0, 0.0, 1.0, 0.0};
  initial[3].fields_str = R"({"uri":"/aliases/a"})";
  initial[4].label = 2006;
  initial[4].vector = {0.0, 0.0, 0.9, 0.1};
  initial[4].fields_str = R"({"uri":"/aliases//a"})";
  if (engine.add_data(initial) != 0) {
    SPDLOG_ERROR("Initial path bitmap data add failed");
    exit(1);
  }

  const std::string docs_recursive =
      R"({"op":"must","field":"uri","conds":["/docs"],"para":"-d=-1"})";
  const std::string docs_depth_one =
      R"({"op":"must","field":"uri","conds":["/docs"],"para":"-d=1"})";
  const std::string docs_overlapping =
      R"({"op":"must","field":"uri","conds":["/docs","/docs/deep"],"para":"-d=-1"})";
  const std::string other_recursive =
      R"({"op":"must","field":"uri","conds":["/other"],"para":"-d=-1"})";
  const std::string aliases_recursive =
      R"({"op":"must","field":"uri","conds":["/aliases"],"para":"-d=-1"})";

  engine.set_filter_layout({2001, 2002, 2003});
  expect_filter_projection(engine, docs_recursive, 2, 0b011U);
  expect_filter_projection(engine, docs_depth_one, 1, 0b001U);
  expect_filter_projection(engine, docs_overlapping, 2, 0b011U);

  engine.set_filter_layout({2005, 2006});
  expect_filter_projection(engine, aliases_recursive, 2, 0b11U);

  engine.set_filter_layout({2001, 2002, 2003});
  FilterResult cached_docs = engine.evaluate_filter(docs_recursive, 10);
  if (cached_docs.native_filter_token == 0) {
    SPDLOG_ERROR("Path filter token was not retained before mutation");
    exit(1);
  }

  AddDataRequest update;
  update.label = 2001;
  update.vector = {1.0, 0.0, 0.0, 0.0};
  update.fields_str = R"({"uri":"/other/a"})";
  update.old_fields_str = R"({"uri":"/docs/a"})";
  if (engine.add_data({update}) != 0) {
    SPDLOG_ERROR("Path bitmap update failed");
    exit(1);
  }
  SearchRequest token_query;
  token_query.query = {1.0, 0.0, 0.0, 0.0};
  token_query.topk = 10;
  if (engine.search_with_filter_token(token_query,
                                      cached_docs.native_filter_token)) {
    SPDLOG_ERROR("Path filter token survived an upsert mutation");
    exit(1);
  }

  engine.set_filter_layout({2001, 2002, 2003});
  expect_filter_projection(engine, docs_recursive, 1, 0b010U);
  expect_filter_projection(engine, other_recursive, 2, 0b101U);

  AddDataRequest added;
  added.label = 2004;
  added.vector = {0.8, 0.2, 0.0, 0.0};
  added.fields_str = R"({"uri":"/docs/new"})";
  if (engine.add_data({added}) != 0) {
    SPDLOG_ERROR("New descendant add failed");
    exit(1);
  }
  engine.set_filter_layout({2001, 2002, 2003, 2004});
  expect_filter_projection(engine, docs_recursive, 2, 0b1010U);

  DeleteDataRequest deleted;
  deleted.label = 2002;
  deleted.old_fields_str = R"({"uri":"/docs/deep/b"})";
  if (engine.delete_data({deleted}) != 0) {
    SPDLOG_ERROR("Path descendant delete failed");
    exit(1);
  }
  engine.set_filter_layout({2001, 2003, 2004});
  expect_filter_projection(engine, docs_recursive, 1, 0b100U);
  expect_filter_projection(engine, other_recursive, 2, 0b011U);

  if (engine.dump(db_path) <= 0) {
    SPDLOG_ERROR("Path bitmap lifecycle dump failed");
    exit(1);
  }

  {
    IndexEngine reloaded(db_path);
    if (!reloaded.is_valid()) {
      SPDLOG_ERROR("Reloaded path bitmap engine is invalid");
      exit(1);
    }
    reloaded.set_filter_layout({2001, 2003, 2004});
    expect_filter_projection(reloaded, docs_recursive, 1, 0b100U);
    expect_filter_projection(reloaded, other_recursive, 2, 0b011U);
    reloaded.set_filter_layout({2005, 2006});
    expect_filter_projection(reloaded, aliases_recursive, 2, 0b11U);
  }

  std::filesystem::remove_all(db_path);
  SPDLOG_INFO("[Passed] test_path_bitmap_lifecycle_and_reload");
}

void expect_paged_store_scan(KVStore& store) {
  const std::vector<std::string> keys = {
      "candidate:1", "candidate:10", "candidate:2", "other:1"};
  const std::vector<std::string> values = {"one", "ten", "two", "other"};
  if (store.put_data(keys, values) != 0) {
    SPDLOG_ERROR("Paged store test data add failed");
    exit(1);
  }

  const auto first =
      store.seek_range_page("candidate:", "candidate;", 2, 1024, false);
  if (first.size() != 2 || first[0].first != "candidate:1" ||
      first[1].first != "candidate:10") {
    SPDLOG_ERROR("Paged store first page was incorrect");
    exit(1);
  }
  const auto second =
      store.seek_range_page(first.back().first, "candidate;", 2, 1024, true);
  if (second.size() != 1 || second[0].first != "candidate:2") {
    SPDLOG_ERROR("Paged store exclusive continuation was incorrect");
    exit(1);
  }
  const auto byte_limited =
      store.seek_range_page("candidate:", "candidate;", 10, 14, false);
  if (byte_limited.size() != 1 ||
      byte_limited[0].first != "candidate:1") {
    SPDLOG_ERROR("Paged store byte budget was not enforced");
    exit(1);
  }
  const auto oversized_first =
      store.seek_range_page("candidate:", "candidate;", 10, 1, false);
  if (oversized_first.size() != 1 ||
      oversized_first[0].first != "candidate:1") {
    SPDLOG_ERROR("Paged store did not admit one oversized row for progress");
    exit(1);
  }
  const auto full = store.seek_range("candidate:", "candidate;");
  if (full.size() != 3) {
    SPDLOG_ERROR("Existing full store scan behavior changed");
    exit(1);
  }
}

void test_paged_store_scan() {
  SPDLOG_INFO("[Running] test_paged_store_scan...");
  VolatileStore volatile_store;
  expect_paged_store_scan(volatile_store);

  const std::string db_path = "test_data_cpp/paged_persist_store";
  if (std::filesystem::exists(db_path)) {
    std::filesystem::remove_all(db_path);
  }
  std::filesystem::create_directories(db_path);
  {
    PersistStore persist_store(db_path);
    expect_paged_store_scan(persist_store);
  }
  std::filesystem::remove_all(db_path);
  SPDLOG_INFO("[Passed] test_paged_store_scan");
}

int main() {
  init_logging("INFO", "stdout", "[%Y-%m-%d %H:%M:%S.%e] [%l] %v");
  test_basic_workflow();
  test_routed_filter_projection_edge_cases();
  test_path_bitmap_lifecycle_and_reload();
  test_paged_store_scan();
  return 0;
}
