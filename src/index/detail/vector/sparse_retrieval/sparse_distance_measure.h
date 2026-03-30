// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <unordered_map>

#include "index/detail/vector/sparse_retrieval/sparse_datapoint.h"
#include "index/detail/vector/sparse_retrieval/common.h"

namespace vectordb {

namespace sparse_dist_measure {

using namespace sparse_common;

struct DotProductReduceTwo {
  template <typename Accumulator, typename T, typename U>
  void operator()(Accumulator* acc, const T a, const U b) {
    *acc += static_cast<Accumulator>(a) * static_cast<Accumulator>(b);
  }
};

struct SquaredL2ReduceTwo {
  template <typename Accumulator, typename T, typename U>
  void operator()(Accumulator* acc, const T a, const U b) {
    const Accumulator diff =
        static_cast<Accumulator>(a) - static_cast<Accumulator>(b);
    *acc += diff * diff;
  }
};

struct SquaredL2ReduceOne {
  template <typename Accumulator, typename T>
  void operator()(Accumulator* acc, const T a) {
    const Accumulator x = static_cast<Accumulator>(a);
    *acc += x * x;
  }
  bool is_noop() {
    return false;
  }
};

struct DoNothingReduce {
  template <typename... T>
  void operator()(T... args) {
  }
  bool is_noop() {
    return true;
  }
};

template <typename T, typename U, typename ReduceTwo>
auto sparse_distance_measure_only_reduce_two(
    const IndexT* indices1, const T* values1, const size_t nonzero_entries1,
    const IndexT* indices2, const U* values2, const size_t nonzero_entries2,
    ReduceTwo reduce_two)
    -> AccumulatorTypeFor<decltype(values1[0]), decltype(values2[0])> {
  using OutputType =
      AccumulatorTypeFor<decltype(values1[0]), decltype(values2[0])>;

  if (nonzero_entries1 == 0 || nonzero_entries2 == 0)
    return 0;

  OutputType result = 0;

  size_t i1_front = 0, i2_front = 0;
  size_t i1_back = nonzero_entries1 - 1, i2_back = nonzero_entries2 - 1;
  // Two-pointer operation on sorted unique indices
  while (i1_front < i1_back && i2_front < i2_back) {
    const size_t to_add_front1 = indices1[i1_front] <= indices2[i2_front];
    const size_t to_add_front2 = indices1[i1_front] >= indices2[i2_front];
    const size_t to_sub_back2 = indices1[i1_back] <= indices2[i2_back];
    const size_t to_sub_back1 = indices1[i1_back] >= indices2[i2_back];
    if (indices1[i1_front] == indices2[i2_front]) {
      reduce_two(&result, values1[i1_front], values2[i2_front]);
    }

    if (indices1[i1_back] == indices2[i2_back]) {
      reduce_two(&result, values1[i1_back], values2[i2_back]);
    }

    i1_front += to_add_front1;
    i2_front += to_add_front2;
    i1_back -= to_sub_back1;
    i2_back -= to_sub_back2;
  }

  if (i1_front == i1_back) {
    for (; i2_front <= i2_back; ++i2_front) {
      if (indices1[i1_front] == indices2[i2_front]) {
        reduce_two(&result, values1[i1_front], values2[i2_front]);
        break;
      }
    }
  } else if (i2_front == i2_back) {
    for (; i1_front <= i1_back; ++i1_front) {
      if (indices1[i1_front] == indices2[i2_front]) {
        reduce_two(&result, values1[i1_front], values2[i2_front]);
        break;
      }
    }
  }

  return result;
}

template <typename T>
inline T* Int(T* arg) {
  return arg;
}
inline int32_t* Int(float* arg) {
  return reinterpret_cast<int32_t*>(arg);
}
inline int64_t* Int(double* arg) {
  return reinterpret_cast<int64_t*>(arg);
}

template <typename T, typename U, typename ReduceTwo, typename ReduceOne>
auto sparse_distance_measure(const IndexT* indices1, const T* values1,
                             const size_t nonzero_entries1,
                             const IndexT* indices2, const U* values2,
                             const size_t nonzero_entries2,
                             ReduceTwo reduce_two, ReduceOne reduce_one)
    -> AccumulatorTypeFor<decltype(values1[0]), decltype(values2[0])> {
  using OutputType =
      AccumulatorTypeFor<decltype(values1[0]), decltype(values2[0])>;

  OutputType result0 = 0, result1 = 0;

  ssize_t i1_front = 0, i2_front = 0;
  ssize_t i1_back = nonzero_entries1, i2_back = nonzero_entries2;
  --i1_back;
  --i2_back;

  while (i1_front < i1_back && i2_front < i2_back) {
    auto front_left = values1[i1_front];
    auto front_right = values2[i2_front];
    auto back_left = values1[i1_back];
    auto back_right = values2[i2_back];

    const size_t to_add_front1 = indices1[i1_front] <= indices2[i2_front];
    const size_t to_add_front2 = indices1[i1_front] >= indices2[i2_front];
    const size_t to_sub_back2 = indices1[i1_back] <= indices2[i2_back];
    const size_t to_sub_back1 = indices1[i1_back] >= indices2[i2_back];

    *Int(&front_left) &= -to_add_front1;
    *Int(&front_right) &= -to_add_front2;
    *Int(&back_left) &= -to_sub_back1;
    *Int(&back_right) &= -to_sub_back2;

    reduce_two(&result0, front_left, front_right);
    reduce_two(&result1, back_left, back_right);
    i1_front += to_add_front1;
    i2_front += to_add_front2;
    i1_back -= to_sub_back1;
    i2_back -= to_sub_back2;
  }

  while (i1_front <= i1_back && i2_front <= i2_back) {
    if (indices1[i1_front] == indices2[i2_front]) {
      reduce_two(&result0, values1[i1_front++], values2[i2_front++]);
    } else if (indices1[i1_front] < indices2[i2_front]) {
      reduce_one(&result0, values1[i1_front++]);
    } else {
      reduce_one(&result0, values2[i2_front++]);
    }
  }

  if (i1_front > i1_back) {
    for (; i2_front <= i2_back; ++i2_front) {
      reduce_one(&result0, values2[i2_front]);
    }
  } else {
    for (; i1_front <= i1_back; ++i1_front) {
      reduce_one(&result0, values1[i1_front]);
    }
  }

  return result0 + result1;
}

// control distance measure by ReduceTwo & ReduceOne
template <typename ReduceTwo, typename ReduceOne>
inline float sparse_distance(const SparseDatapointView& a,
                             const SparseDatapointView& b, ReduceTwo reduce_two,
                             ReduceOne reduce_one) {
  if (reduce_one.is_noop()) {
    return sparse_distance_measure_only_reduce_two(
        a.indices(), a.values(), a.nonzero_entries(), b.indices(), b.values(),
        b.nonzero_entries(), reduce_two);
  } else {
    return sparse_distance_measure(a.indices(), a.values(), a.nonzero_entries(),
                                   b.indices(), b.values(), b.nonzero_entries(),
                                   reduce_two, reduce_one);
  }
}

}  // namespace sparse_dist_measure
}  // namespace vectordb
