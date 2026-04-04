// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include <algorithm>
#include <iostream>
#include <fstream>
#include <vector>
#include <string>
#include <cstdint>

namespace vectordb {

// bin type
template <typename T>
inline size_t write_bin(std::ostream& out, const T& podRef) {
  out.write((const char*)&podRef, sizeof(T));
  return sizeof(T);
}

// bin type
template <typename T>
inline size_t write_bin(std::ofstream& out, const T& podRef) {
  out.write((const char*)&podRef, sizeof(T));
  return sizeof(T);
}

template <typename T>
inline void read_bin(std::istream& in, T& podRef) {
  in.read((char*)&podRef, sizeof(T));
}

template <typename T>
inline void read_bin(std::ifstream& in, T& podRef) {
  in.read((char*)&podRef, sizeof(T));
}

// str type
inline size_t write_str(std::ostream& out, const std::string& content) {
  if (content.size() >= UINT32_MAX) {
    write_bin(out, UINT32_MAX);
    write_bin(out, uint64_t(content.size()));
  } else {
    uint32_t content_len = content.size();
    write_bin(out, content_len);
  }
  out.write((char*)content.c_str(), content.size());
  return content.size() + sizeof(int);
}

inline void read_str(std::istream& in, std::string& content) {
  content.clear();
  std::vector<char> buffer;
  uint32_t content_len_or_flag = 0;
  uint64_t content_len64 = 0;
  read_bin(in, content_len_or_flag);
  if (content_len_or_flag == UINT32_MAX) {
    read_bin(in, content_len64);
  } else {
    content_len64 = content_len_or_flag;
  }

  buffer.resize(content_len64);
  content.reserve(content_len64);
  in.read((char*)buffer.data(), (size_t)content_len64);
  std::transform(buffer.begin(), buffer.end(), std::back_inserter(content),
                 [](char c) { return c; });
}

inline void write_label_vec(std::ostream& out,
                            const std::vector<uint64_t>& labels_u64) {
  int label_bits = 64;
  int elements_num = (int)labels_u64.size();
  write_bin(out, label_bits);
  write_bin(out, elements_num);
  out.write((char*)labels_u64.data(), labels_u64.size() * sizeof(uint64_t));
}

inline void read_label_vec(std::istream& in,
                           std::vector<uint64_t>& labels_u64) {
  int label_bits = 64;
  int elements_num = 0;
  read_bin(in, label_bits);
  read_bin(in, elements_num);
  labels_u64.resize(elements_num);
  in.read((char*)labels_u64.data(), elements_num * sizeof(uint64_t));
}

}  // namespace vectordb