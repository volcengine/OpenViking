// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#pragma once
#include <string>
#include <vector>
#include <sstream>
#include <type_traits>

namespace vectordb {

inline void split(std::vector<std::string>& tokens, const std::string& str,
                  const std::string& delimiters) {
  size_t start = 0;
  size_t end = str.find_first_of(delimiters);

  while (end != std::string::npos) {
    if (end > start) {
      tokens.push_back(str.substr(start, end - start));
    }
    start = end + 1;
    end = str.find_first_of(delimiters, start);
  }

  if (start < str.length()) {
    tokens.push_back(str.substr(start));
  }
}

namespace myformat {
namespace detail {
template <typename T>
inline std::string to_string_impl(const T& value) {
  if constexpr (std::is_arithmetic_v<T>) {
    return std::to_string(value);
  }
  std::ostringstream oss;
  oss << value;
  return oss.str();
}
template <typename... Args>
struct ArgCollector {
  static std::vector<std::string> collect(const Args&... args) {
    std::vector<std::string> result;
    result.reserve(sizeof...(Args));
    (result.push_back(to_string_impl(args)), ...);
    return result;
  }
};

inline std::string format_impl(const std::string& fmt,
                               const std::vector<std::string>& args) {
  std::string result;
  size_t arg_index = 0;
  size_t pos = 0;
  const size_t fmt_len = fmt.length();
  while (pos < fmt_len) {
    size_t placeholder = fmt.find("{}", pos);
    if (placeholder == std::string::npos) {
      result += fmt.substr(pos);
      break;
    }

    result += fmt.substr(pos, placeholder - pos);

    if (arg_index < args.size()) {
      result += args[arg_index++];
    } else {
      result += "{}";
    }
    pos = placeholder + 2;
  }
  return result;
}
}  // namespace detail
}  // namespace myformat

template <typename... Args>
inline std::string sformat(const std::string& fmt, const Args&... args) {
  auto args_str = myformat::detail::ArgCollector<Args...>::collect(args...);
  return myformat::detail::format_impl(fmt, args_str);
}

}  // namespace vectordb
