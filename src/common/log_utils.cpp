// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#include "log_utils.h"
#include "spdlog/spdlog.h"
#include "spdlog/sinks/stdout_sinks.h"
#include "spdlog/sinks/basic_file_sink.h"
#include <algorithm>
#include <chrono>
#include <stdexcept>

namespace vectordb {
namespace {
std::string quoted_message(const spdlog::string_view_t& value) {
  std::string result = "\"";
  constexpr char digits[] = "0123456789abcdef";
  for (size_t offset = 0; offset < value.size();) {
    const auto byte = static_cast<unsigned char>(value[offset]);
    if (byte == '"' || byte == '\\') {
      result += '\\';
      result += byte;
    } else if (byte < 0x20) {
      result += "\\u00";
      result += digits[byte >> 4];
      result += digits[byte & 15];
    } else if (byte < 0x80) {
      result += byte;
    } else {
      const size_t width = byte >= 0xc2 && byte <= 0xdf ? 2
                         : byte >= 0xe0 && byte <= 0xef ? 3
                         : byte >= 0xf0 && byte <= 0xf4 ? 4 : 0;
      bool valid = width != 0 && width <= value.size() - offset;
      for (size_t index = 1; valid && index < width; ++index) {
        valid = (static_cast<unsigned char>(value[offset + index]) & 0xc0) == 0x80;
      }
      if (valid) {
        const auto second = static_cast<unsigned char>(value[offset + 1]);
        // Reject overlong encodings, surrogate code points and values above U+10FFFF.
        valid = !(byte == 0xe0 && second < 0xa0)
             && !(byte == 0xed && second >= 0xa0)
             && !(byte == 0xf0 && second < 0x90)
             && !(byte == 0xf4 && second >= 0x90);
      }
      if (valid) {
        result.append(value.data() + offset, width);
        offset += width;
        continue;
      }
      result += "\\ufffd";
    }
    ++offset;
  }
  result += '"';
  return result;
}

class CommonLogFormatter final : public spdlog::formatter {
 public:
  void format(const spdlog::details::log_msg& message,
              spdlog::memory_buf_t& dest) override {
    const auto seconds = std::chrono::system_clock::to_time_t(message.time);
    const auto utc = spdlog::details::os::gmtime(seconds);
    const auto millis = std::chrono::duration_cast<std::chrono::milliseconds>(
        message.time.time_since_epoch()).count() % 1000;
    const char* level = "DEBUG";
    switch (message.level) {
      case spdlog::level::info: level = "INFO"; break;
      case spdlog::level::warn: level = "WARNING"; break;
      case spdlog::level::err: level = "ERROR"; break;
      case spdlog::level::critical: level = "CRITICAL"; break;
      default: break;
    }
    auto payload = message.payload;
    const bool truncated = payload.size() > 2048;
    if (truncated) {
      size_t end = 2048;
      while (end > 0 && (static_cast<unsigned char>(payload[end]) & 0xc0) == 0x80)
        --end;
      payload = spdlog::string_view_t(payload.data(), end);
    }
    spdlog::fmt_lib::format_to(std::back_inserter(dest),
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:03}Z {} {} pid={} request_id=- trace_id=- message={}",
        utc.tm_year + 1900, utc.tm_mon + 1, utc.tm_mday, utc.tm_hour,
        utc.tm_min, utc.tm_sec, millis, level, message.logger_name,
        spdlog::details::os::pid(), quoted_message(payload));
    if (truncated)
      spdlog::fmt_lib::format_to(std::back_inserter(dest),
          " truncated=true original_length={}", message.payload.size());
    dest.push_back('\n');
  }
  std::unique_ptr<spdlog::formatter> clone() const override {
    return std::make_unique<CommonLogFormatter>();
  }
};
}  // namespace

void init_logging(const std::string& log_level, const std::string& log_output,
                  const std::string& log_format) {
  std::string upper = log_level;
  std::transform(upper.begin(), upper.end(), upper.begin(), ::toupper);
  spdlog::level::level_enum level;
  if (upper == "TRACE") level = spdlog::level::trace;
  else if (upper == "DEBUG") level = spdlog::level::debug;
  else if (upper == "INFO") level = spdlog::level::info;
  else if (upper == "WARNING" || upper == "WARN") level = spdlog::level::warn;
  else if (upper == "ERROR") level = spdlog::level::err;
  else if (upper == "CRITICAL" || upper == "FATAL") level = spdlog::level::critical;
  else throw std::invalid_argument("unsupported C++ logging level");

  const std::string common = "%(asctime)s %(levelname)s %(name)s pid=%(process)d request_id=%(request_id)s trace_id=%(trace_id)s message=%(message)s";
  if (log_format != common)
    throw std::invalid_argument("C++ logging requires the common cross-runtime format");
  std::shared_ptr<spdlog::sinks::sink> sink;
  if (log_output == "stdout") sink = std::make_shared<spdlog::sinks::stdout_sink_mt>();
  else if (log_output == "stderr") sink = std::make_shared<spdlog::sinks::stderr_sink_mt>();
  else sink = std::make_shared<spdlog::sinks::basic_file_sink_mt>(log_output, false);
  auto logger = std::make_shared<spdlog::logger>("vikingdb", sink);
  logger->set_level(level);
  logger->set_formatter(std::make_unique<CommonLogFormatter>());
  spdlog::set_default_logger(logger);
  spdlog::set_level(level);
  spdlog::flush_on(spdlog::level::err);
  logger->debug("c++ logging initialized");
}
}  // namespace vectordb
