// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include "dir_index.h"

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

void DirIndex::bind_leaf_bitmap(TrieNode* node, const Bitmap* bitmap) {
  if (!node || !bitmap) {
    return;
  }
  if (!node->leaf_bitmap_) {
    node->leaf_bitmap_ = bitmap;
    return;
  }
  if (node->leaf_bitmap_ == bitmap) {
    return;
  }

  auto& collisions = leaf_bitmap_collisions_[node];
  if (std::find(collisions.begin(), collisions.end(), bitmap) ==
      collisions.end()) {
    collisions.push_back(bitmap);
  }
}

void DirIndex::get_merged_bitmaps(
    const std::string& path_prefix, int depth,
    std::vector<const Bitmap*>& bitmaps) const {
  TrieNode* start_node = find_node(path_prefix);
  if (!start_node) {
    return;
  }

  collect_bitmaps_recursive(start_node, 0, depth, bitmaps);
}

void DirIndex::collect_bitmaps_recursive(
    const TrieNode* node, int current_depth, int max_depth,
    std::vector<const Bitmap*>& bitmaps) const {
  if (!node) {
    return;
  }

  if (node->is_leaf_) {
    if (node->leaf_bitmap_) {
      bitmaps.push_back(node->leaf_bitmap_);
    }
    const auto collision_it = leaf_bitmap_collisions_.find(node);
    if (collision_it != leaf_bitmap_collisions_.end()) {
      bitmaps.insert(bitmaps.end(), collision_it->second.begin(),
                     collision_it->second.end());
    }
  }

  if (max_depth != -1 && current_depth >= max_depth) {
    return;
  }

  for (const auto& child_pair : node->children_) {
    const auto& child_node = child_pair.second;
    collect_bitmaps_recursive(child_node.get(), current_depth + 1, max_depth,
                              bitmaps);
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
  leaf_bitmap_collisions_.clear();
  root_ = parse_recursive(input, nullptr);
}

}  // namespace vectordb
