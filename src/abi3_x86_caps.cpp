// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
#define Py_LIMITED_API 0x030A0000
#include <Python.h>

#include <string>
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

namespace {

struct CpuFeatures {
  bool sse3 = false;
  bool avx = false;
  bool avx2 = false;
  bool avx512f = false;
  bool avx512dq = false;
  bool avx512bw = false;
  bool avx512vl = false;
  bool avx512_vnni = false;
  bool amx_tile = false;
  bool amx_int8 = false;
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

  // AVX512-VNNI: CPUID leaf 7, subleaf 0, ECX bit 11
  features.avx512_vnni = (regs[2] & (1 << 11)) != 0;

  // AMX: CPUID leaf 7, subleaf 0, EDX bit 24 (AMX-TILE), bit 25 (AMX-INT8)
  features.amx_tile = (regs[3] & (1 << 24)) != 0;
  features.amx_int8 = (regs[3] & (1 << 25)) != 0;

  const bool avx512_os = (xcr0 & 0xe6) == 0xe6;
  if (!avx512_os) {
    features.avx512f = false;
    features.avx512dq = false;
    features.avx512bw = false;
    features.avx512vl = false;
    features.avx512_vnni = false;
  }

  // AMX requires XCR0 bits 17 (TILECFG) and 18 (TILEDATA)
  const bool amx_os = (xcr0 & 0x60000) == 0x60000;
  if (!amx_os) {
    features.amx_tile = false;
    features.amx_int8 = false;
  }

  return features;
}
#else
CpuFeatures detect_cpu_features() { return CpuFeatures{}; }
#endif

std::vector<std::string> get_supported_variants_impl() {
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
    if (features.avx512_vnni) {
      variants.emplace_back("x86_avx512_vnni");
      if (features.amx_tile && features.amx_int8) {
        variants.emplace_back("x86_amx");
      }
    }
  }
  return variants;
}

PyObject* py_get_supported_variants(PyObject*, PyObject*) {
  const auto variants = get_supported_variants_impl();
  PyObject* list = PyList_New(static_cast<Py_ssize_t>(variants.size()));
  if (list == nullptr) {
    return nullptr;
  }
  for (Py_ssize_t i = 0; i < static_cast<Py_ssize_t>(variants.size()); ++i) {
    const auto& variant = variants[static_cast<size_t>(i)];
    PyObject* item = PyUnicode_FromStringAndSize(
        variant.data(), static_cast<Py_ssize_t>(variant.size()));
    if (item == nullptr) {
      Py_DECREF(list);
      return nullptr;
    }
    PyList_SetItem(list, i, item);
  }
  return list;
}

PyMethodDef kMethods[] = {
    {"get_supported_variants", py_get_supported_variants, METH_NOARGS,
     "Return CPU-supported x86 engine variants."},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef kModuleDef = {
    PyModuleDef_HEAD_INIT,
    "_x86_caps",
    "OpenViking abi3 x86 capability probe.",
    -1,
    kMethods,
};

}  // namespace

PyMODINIT_FUNC PyInit__x86_caps(void) {
  return PyModule_Create(&kModuleDef);
}
