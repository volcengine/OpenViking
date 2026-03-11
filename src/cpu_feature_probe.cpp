// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <vector>

#if defined(__x86_64__) || defined(_M_X64) || defined(__i386__) || defined(_M_IX86)
#if defined(_MSC_VER)
#include <immintrin.h>
#include <intrin.h>
#else
#include <cpuid.h>
#include <immintrin.h>
#endif
#endif

namespace py = pybind11;

namespace {

struct CpuFeatures {
  bool sse3 = false;
  bool avx = false;
  bool avx2 = false;
  bool avx512f = false;
  bool avx512dq = false;
  bool avx512bw = false;
  bool avx512vl = false;
};

#if defined(__x86_64__) || defined(_M_X64) || defined(__i386__) || defined(_M_IX86)
void cpuid(int regs[4], int leaf, int subleaf) {
#if defined(_MSC_VER)
  __cpuidex(regs, leaf, subleaf);
#else
  __cpuid_count(leaf, subleaf, regs[0], regs[1], regs[2], regs[3]);
#endif
}

unsigned long long xgetbv(unsigned int index) {
#if defined(_MSC_VER)
  return _xgetbv(index);
#else
  unsigned int eax = 0;
  unsigned int edx = 0;
  __asm__ volatile(".byte 0x0f, 0x01, 0xd0"
                   : "=a"(eax), "=d"(edx)
                   : "c"(index));
  return (static_cast<unsigned long long>(edx) << 32) | eax;
#endif
}

CpuFeatures detect_cpu_features() {
  CpuFeatures features;
  int regs[4] = {0, 0, 0, 0};

  cpuid(regs, 1, 0);
  features.sse3 = (regs[2] & (1 << 0)) != 0;
  const bool osxsave = (regs[2] & (1 << 27)) != 0;
  const bool avx_hw = (regs[2] & (1 << 28)) != 0;

  if (!(osxsave && avx_hw)) {
    return features;
  }

  const auto xcr0 = xgetbv(0);
  const bool avx_os = (xcr0 & 0x6) == 0x6;
  if (!avx_os) {
    return features;
  }

  features.avx = true;

  cpuid(regs, 7, 0);
  features.avx2 = (regs[1] & (1 << 5)) != 0;
  features.avx512f = (regs[1] & (1 << 16)) != 0;
  features.avx512dq = (regs[1] & (1 << 17)) != 0;
  features.avx512bw = (regs[1] & (1 << 30)) != 0;
  features.avx512vl = (regs[1] & (1u << 31)) != 0;

  const bool avx512_os = (xcr0 & 0xe6) == 0xe6;
  if (!avx512_os) {
    features.avx512f = false;
    features.avx512dq = false;
    features.avx512bw = false;
    features.avx512vl = false;
  }

  return features;
}
#else
CpuFeatures detect_cpu_features() { return CpuFeatures{}; }
#endif

std::vector<std::string> get_supported_variants() {
  std::vector<std::string> variants;
  const auto features = detect_cpu_features();

  if (features.sse3) {
    variants.emplace_back("x86_sse3");
  }
  if (features.avx && features.avx2) {
    variants.emplace_back("x86_avx2");
  }
  if (features.avx && features.avx512f && features.avx512dq &&
      features.avx512bw && features.avx512vl) {
    variants.emplace_back("x86_avx512");
  }
  return variants;
}

}  // namespace

PYBIND11_MODULE(_x86_caps, m) {
  m.def("get_supported_variants", &get_supported_variants,
        "Return CPU-supported x86 engine variants");
}
