// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <string>
#include <vector>

namespace vectordb {

inline void split(std::vector<std::string>& tokens, const std::string& str,
                  const std::string& delimiters) {
  size_t start = 0;
  size_t end = str.find_first_of(delimiters);

  while (end != std::string::npos) {
    if (end > start) {
      tokens.push_back(str.substr(start, end - start));
    }
    start = end + 1;
    end = str.find_first_of(delimiters, start);
  }

  if (start < str.length()) {
    tokens.push_back(str.substr(start));
  }
}

}  // namespace vectordb
