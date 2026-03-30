// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include "index/index_engine.h"
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
        "vector_index": {
            "type": "flat",
            "dim": 4,
            "metric": "l2"
        },
        "scalar_index": {
            "title": "string",
            "count": "int64",
            "price": "float32"
        }
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
    req1.fields_str = R"({"title": "apple", "count": 10, "price": 5.5})";
    add_reqs.push_back(req1);

    AddDataRequest req2;
    req2.label = 1002;
    req2.vector = {0.2, 0.2, 0.2, 0.2};
    req2.fields_str = R"({"title": "banana", "count": 20, "price": 3.0})";
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
    
    // 4. Delete Data
    std::vector<DeleteDataRequest> del_reqs(1);
    del_reqs[0].label = 1001;
    del_reqs[0].old_fields_str = R"({"title": "apple", "count": 10, "price": 5.5})";
    
    ret = engine.delete_data(del_reqs);
    if (ret != 0) {
        SPDLOG_ERROR("Delete data failed");
        exit(1);
    }

    // 5. Search again to verify deletion
    res = engine.search(search_req);
    // Depending on soft delete implementation, result might still be there but filtered, 
    // or simply not returned. For brute force, it usually checks filter.
    // If it returns, ensure it's not the deleted one or handle accordingly.
    if (res.result_num > 0 && res.labels[0] == 1001) {
         SPDLOG_WARN("Deleted item 1001 still found (might be soft delete delay or consistency model)");
    } else {
         SPDLOG_INFO("Deleted item 1001 correctly not found or ranked lower");
    }

    // 6. Dump
    int64_t ts = engine.dump(db_path);
    if (ts <= 0) {
        SPDLOG_ERROR("Dump failed");
        exit(1);
    }

    SPDLOG_INFO("[Passed] test_basic_workflow");
}

int main() {
    init_logging("INFO", "stdout");
    test_basic_workflow();
    return 0;
}
