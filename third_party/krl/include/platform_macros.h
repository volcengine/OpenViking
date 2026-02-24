/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
 */

#pragma once

#define SUCCESS 0
#define INVALPOINTER -1
#define FAILALLOC -2
#define INVALPARAM -3
#define DOUBLEFREE -4
#define UNSAFEMEM -5
#define FAILIO -6

#define METRIC_INNER_PRODUCT 0
#define METRIC_L2 1

#define likely(x) __builtin_expect(!!(x), 1)
#define unlikely(x) __builtin_expect(!!(x), 0)

inline void prefetch_L1(const void *address)
{
    __builtin_prefetch(address, 0, 3);
}
inline void prefetch_L2(const void *address)
{
    __builtin_prefetch(address, 0, 2);
}
inline void prefetch_L3(const void *address)
{
    __builtin_prefetch(address, 0, 1);
}
inline void prefetch_Lx(const void *address)
{
    __builtin_prefetch(address, 0, 0);
}

#define KRL_DEFAULT_ALIGNED (64)
#define ALIGNED(x) __attribute__((aligned(x)))

#ifdef __GNUC__

#define KRL_IMPRECISE_FUNCTION_BEGIN \
    _Pragma("GCC push_options") _Pragma("GCC optimize (\"unroll-loops,associative-math,no-signed-zeros\")")
#define KRL_IMPRECISE_FUNCTION_END _Pragma("GCC pop_options")
#else
#define KRL_IMPRECISE_FUNCTION_BEGIN
#define KRL_IMPRECISE_FUNCTION_END
#endif