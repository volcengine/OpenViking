/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
 */

#pragma once
#include <cstring>
#include <iostream>
#include <algorithm>

namespace SafeMemory {

template <typename D, typename S>
int CheckAndMemcpy(D *dest, size_t destBufferSize, const S *src, size_t srcBufferSize)
{
    if (srcBufferSize > destBufferSize) {
        std::cerr << "Memcpy failed: destBufferSize[" << destBufferSize << "] should be >= srcBufferSize["
                  << srcBufferSize << "].\n";
        return -1;
    }
    if (dest == nullptr || src == nullptr) {
        std::cerr << "Memcpy failed: null pointer detected\n";
        return -1;
    }
    memcpy(dest, src, srcBufferSize);
    return 0;
}

template <typename D>
int CheckAndMemset(D *dest, size_t destBufferSize, int memsetValue, size_t setSize)
{
    if (setSize > destBufferSize) {
        std::cerr << "Memset failed: destBufferSize[" << destBufferSize << "] should be >= setSize[" << setSize
                  << "].\n";
        return -1;
    }
    if (dest == nullptr) {
        std::cerr << "Memset failed: null pointer detected\n";
        return -1;
    }
    memset(dest, memsetValue, setSize);
    return 0;
}

}  // namespace SafeMemory