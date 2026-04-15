// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <algorithm>
#include <string>
#include <vector>
#include <set>
#include <memory>
#include <unordered_map>
#include <unordered_set>
#include <sstream>
#include "common/io_utils.h"
#include "index/detail/scalar/bitmap_holder/bitmap.h"

namespace vectordb {

struct TrieNode {
  std::string path_segment_;
  TrieNode* parent_ = nullptr;
  std::unordered_map<std::string, std::unique_ptr<TrieNode>> children_;
  bool is_leaf_ = false;

  TrieNode() = default;
  explicit TrieNode(const std::string& path_segment, TrieNode* parent)
      : path_segment_(path_segment), parent_(parent) {
  }
};

class DirIndex {
 public:
  DirIndex() = default;
  virtual ~DirIndex() = default;

  void add_key(const std::string& key) {
    TrieNode* node = root_.get();
    for (const auto& segment : split_path(key)) {
      auto it = node->children_.find(segment);
      if (it == node->children_.end()) {
        auto new_node = std::make_unique<TrieNode>(segment, node);
        TrieNode* new_node_ptr = new_node.get();
        node->children_.emplace(segment, std::move(new_node));
        node = new_node_ptr;
      } else {
        node = it->second.get();
      }
    }
    node->is_leaf_ = true;
  }

  void get_merged_bitmap(const std::string& path_prefix, int depth,
                         std::unordered_set<std::string>& unique_bitmaps) const;

  virtual void serialize_to_stream(std::ofstream& output);
  virtual void parse_from_stream(std::ifstream& input);

 private:
  std::unique_ptr<TrieNode> root_ = std::make_unique<TrieNode>("", nullptr);
  TrieNode* find_node(const std::string& path) const;

  std::vector<std::string> split_path(const std::string& path) const;
  void serialize_recursive(const TrieNode* node, std::ofstream& output) const;
  std::unique_ptr<TrieNode> parse_recursive(std::ifstream& input,
                                            TrieNode* parent);

  void collect_bitmaps_recursive_optimized(
      const TrieNode* node, int current_depth, int max_depth,
      std::unordered_set<std::string>& unique_bitmaps,
      std::string& path_buffer) const;
};

using DirIndexPtr = std::shared_ptr<DirIndex>;

}  // namespace vectordb