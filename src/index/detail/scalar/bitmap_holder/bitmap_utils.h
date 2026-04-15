// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <functional>

namespace vectordb {

using offset_filter_t = std::function<bool(uint32_t)>;

}
