// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include "log_utils.h"
#include "spdlog/spdlog.h"
#include "spdlog/sinks/stdout_color_sinks.h"
#include "spdlog/sinks/basic_file_sink.h"
#include <algorithm>
#include <iostream>
#include <vector>

namespace vectordb {

void init_logging(const std::string& log_level, const std::string& log_output,
                  const std::string& log_format) {
  try {
    // Set log level
    spdlog::level::level_enum level = spdlog::level::info;
    std::string level_upper = log_level;
    std::transform(level_upper.begin(), level_upper.end(), level_upper.begin(),
                   ::toupper);

    if (level_upper == "DEBUG") {
      level = spdlog::level::debug;
    } else if (level_upper == "INFO") {
      level = spdlog::level::info;
    } else if (level_upper == "WARNING" || level_upper == "WARN") {
      level = spdlog::level::warn;
    } else if (level_upper == "ERROR") {
      level = spdlog::level::err;
    } else if (level_upper == "CRITICAL") {
      level = spdlog::level::critical;
    }

    // Set sink
    std::shared_ptr<spdlog::sinks::sink> sink;
    if (log_output == "stdout") {
      sink = std::make_shared<spdlog::sinks::stdout_color_sink_mt>();
    } else if (log_output == "stderr") {
      sink = std::make_shared<spdlog::sinks::stderr_color_sink_mt>();
    } else {
      // File sink
      sink =
          std::make_shared<spdlog::sinks::basic_file_sink_mt>(log_output, true);
    }

    auto logger = std::make_shared<spdlog::logger>("vikingdb", sink);
    logger->set_level(level);

    logger->set_pattern(log_format);

    spdlog::set_default_logger(logger);
    spdlog::set_level(level);

    spdlog::flush_on(spdlog::level::err);

    logger->debug("C++ logging initialized successfully");

  } catch (const spdlog::spdlog_ex& ex) {
    std::cerr << "Log initialization failed: " << ex.what() << std::endl;
  }
}

}  // namespace vectordb
