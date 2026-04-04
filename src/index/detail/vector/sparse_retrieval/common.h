// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
namespace vectordb {

namespace sparse_common {

template <typename T>
inline constexpr bool IsFloatingType() {
  return std::is_floating_point<std::decay_t<T>>::value;
}

template <typename T>
using AccumulatorTypeFor1 =
    std::conditional_t<IsFloatingType<T>(), std::decay_t<T>, int64_t>;

template <typename T, typename U = T, typename V = T>
using AccumulatorTypeFor = decltype(std::declval<AccumulatorTypeFor1<T>>() +
                                    std::declval<AccumulatorTypeFor1<U>>() +
                                    std::declval<AccumulatorTypeFor1<V>>());

}  // namespace sparse_common

}  // namespace vectordb