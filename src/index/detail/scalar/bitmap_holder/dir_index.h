// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <algorithm>
#include <memory>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>
#include "common/io_utils.h"
#include "index/detail/scalar/bitmap_holder/bitmap.h"

namespace vectordb {

struct TrieNode {
  std::string path_segment_;
  TrieNode* parent_ = nullptr;
  std::unordered_map<std::string, std::unique_ptr<TrieNode>> children_;
  // Non-owning pointer into BitmapGroupBase::bitmap_group_. Bitmap objects are
  // updated in place and remain stable during every live directory-index
  // access under IndexManager's reader/writer lock.
  const Bitmap* leaf_bitmap_ = nullptr;
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

  void add_key(const std::string& key, const Bitmap* bitmap) {
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
    bind_leaf_bitmap(node, bitmap);
  }

  void get_merged_bitmaps(const std::string& path_prefix, int depth,
                          std::vector<const Bitmap*>& bitmaps) const;

  virtual void serialize_to_stream(std::ofstream& output);
  virtual void parse_from_stream(std::ifstream& input);

 private:
  std::unique_ptr<TrieNode> root_ = std::make_unique<TrieNode>("", nullptr);
  // Canonically equivalent spellings can resolve to the same trie leaf. Keep
  // their additional bitmaps out of TrieNode so the normal one-bitmap case
  // does not pay per-node vector overhead.
  std::unordered_map<const TrieNode*, std::vector<const Bitmap*>>
      leaf_bitmap_collisions_;
  TrieNode* find_node(const std::string& path) const;
  void bind_leaf_bitmap(TrieNode* node, const Bitmap* bitmap);

  std::vector<std::string> split_path(const std::string& path) const;
  void serialize_recursive(const TrieNode* node, std::ofstream& output) const;
  std::unique_ptr<TrieNode> parse_recursive(std::ifstream& input,
                                            TrieNode* parent);

  void collect_bitmaps_recursive(
      const TrieNode* node, int current_depth, int max_depth,
      std::vector<const Bitmap*>& bitmaps) const;
};

using DirIndexPtr = std::shared_ptr<DirIndex>;

}  // namespace vectordb
