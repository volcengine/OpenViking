// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once

#include <string>

namespace vectordb {

// Logging policy for levels, content, and output:
// https://github.com/volcengine/OpenViking/blob/main/docs/repository/LOGGING.md
void init_logging(const std::string& log_level, const std::string& log_output,
                  const std::string& log_format);

}  // namespace vectordb
