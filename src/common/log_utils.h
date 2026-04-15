// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include <string>

namespace vectordb {

void init_logging(const std::string& log_level, const std::string& log_output,
                  const std::string& log_format);

}  // namespace vectordb
