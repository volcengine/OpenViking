// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include <vector>
#include <algorithm>
#include <numeric>
#include <iterator>
#include <type_traits>

namespace vectordb {
// Helper traits to check for random access iterator
template <typename It>
constexpr bool is_random_access_iterator_v =
    std::is_same_v<typename std::iterator_traits<It>::iterator_category,
                   std::random_access_iterator_tag>;

// Core ZipSort: Branch optimized version supporting two sequences.
template <typename Comparator, typename T, typename U>
void ZipSortBranchOptimized(Comparator comp, T begin, T end, U begin1, U end1) {
  static_assert(is_random_access_iterator_v<T>,
                "First iterator must be random access");
  static_assert(is_random_access_iterator_v<U>,
                "All rest iterators must be random access");

  const size_t n = end - begin;
  if (n <= 1)
    return;

  std::vector<size_t> indices(n);
  std::iota(indices.begin(), indices.end(), 0);

  std::sort(indices.begin(), indices.end(),
            [&](const size_t& a, const size_t& b) noexcept {
              return comp(*(begin + a), *(begin + b));
            });

  auto rearrange = [&](auto it) noexcept {
    using ValueType = typename std::iterator_traits<decltype(it)>::value_type;
    std::vector<ValueType> temp(n);
    for (size_t i = 0; i < n; ++i) {
      temp[i] = *(it + indices[i]);
    }
    std::copy(temp.begin(), temp.end(), it);
  };

  rearrange(begin);
  rearrange(begin1);
}

}
