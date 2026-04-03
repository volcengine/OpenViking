// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include "dir_index.h"
#include <deque>
#include <unordered_set>
#include "index/detail/scalar/bitmap_holder/bitmap_field_group.h"

namespace vectordb {

std::vector<std::string> DirIndex::split_path(const std::string& path) const {
  std::vector<std::string> segments;
  if (path.empty() || path == "/") {
    return segments;
  }
  std::stringstream ss(path.at(0) == '/' ? path.substr(1) : path);
  std::string seg;
  while (std::getline(ss, seg, '/')) {
    if (!seg.empty()) {
      segments.push_back(seg);
    }
  }
  return segments;
}

TrieNode* DirIndex::find_node(const std::string& path) const {
  if (path.empty() || path == "/") {
    return root_.get();
  }
  auto segments = split_path(path);
  TrieNode* node = root_.get();
  for (const auto& seg : segments) {
    auto it = node->children_.find(seg);
    if (it == node->children_.end()) {
      return nullptr;
    }
    node = it->second.get();
  }
  return node;
}

void DirIndex::get_merged_bitmap(
    const std::string& path_prefix, int depth,
    std::unordered_set<std::string>& unique_bitmaps) const {
  TrieNode* start_node = find_node(path_prefix);
  if (!start_node) {
    return;
  }

  std::string path_buffer = path_prefix.empty() ? "" : path_prefix;
  if (!path_buffer.empty() && path_buffer[0] != '/') {
    path_buffer.insert(path_buffer.begin(), '/');
  }
  while (path_buffer.size() > 1 && path_buffer.back() == '/') {
    path_buffer.pop_back();
  }

  collect_bitmaps_recursive_optimized(start_node, 0, depth, unique_bitmaps,
                                      path_buffer);
}

void DirIndex::collect_bitmaps_recursive_optimized(
    const TrieNode* node, int current_depth, int max_depth,
    std::unordered_set<std::string>& bitmaps, std::string& path_buffer) const {
  if (!node) {
    return;
  }

  if (node->is_leaf_) {
    if (path_buffer.empty() || path_buffer == "/") {
      bitmaps.insert("/");
    } else {
      bitmaps.insert(path_buffer);
    }
  }

  if (max_depth != -1 && current_depth >= max_depth) {
    return;
  }

  for (const auto& child_pair : node->children_) {
    const auto& segment = child_pair.first;
    const auto& child_node = child_pair.second;
    size_t original_size = path_buffer.length();

    if (path_buffer.empty() || path_buffer == "/") {
      path_buffer = "/" + segment;
    } else {
      path_buffer += "/" + segment;
    }

    collect_bitmaps_recursive_optimized(child_node.get(), current_depth + 1,
                                        max_depth, bitmaps, path_buffer);
    path_buffer.resize(original_size);
  }
}

void DirIndex::serialize_recursive(const TrieNode* node,
                                   std::ofstream& output) const {
  if (!node) {
    return;
  }
  write_str(output, node->path_segment_);
  write_bin(output, node->is_leaf_);
  size_t children_num = node->children_.size();
  write_bin(output, children_num);
  for (const auto& pair : node->children_) {
    serialize_recursive(pair.second.get(), output);
  }
}

void DirIndex::serialize_to_stream(std::ofstream& output) {
  if (root_) {
    serialize_recursive(root_.get(), output);
  }
}

std::unique_ptr<TrieNode> DirIndex::parse_recursive(std::ifstream& input,
                                                    TrieNode* parent) {
  auto node = std::make_unique<TrieNode>();
  node->parent_ = parent;
  read_str(input, node->path_segment_);
  read_bin(input, node->is_leaf_);
  size_t children_num = 0;
  read_bin(input, children_num);
  for (size_t i = 0; i < children_num; ++i) {
    auto child = parse_recursive(input, node.get());
    node->children_[child->path_segment_] = std::move(child);
  }
  return node;
}
void DirIndex::parse_from_stream(std::ifstream& input) {
  root_ = parse_recursive(input, nullptr);
}

}  // namespace vectordb