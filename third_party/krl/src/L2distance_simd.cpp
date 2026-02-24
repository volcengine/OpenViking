// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
// Adapted from KRL (Kunpeng Retrieval Library) for ARM NEON optimizations.

#include "krl.h"
#include "krl_internal.h"
#include "platform_macros.h"
#include "safe_memory.h"
#include <cstdio>

extern "C" {

/*
* @brief Compute the L2 square of two float vectors.
* @param x Pointer to the first vector (float).
* @param y Pointer to the second vector (float).
* @param d Dimension of the vectors.
* @param dis Stores the computed L2 square result (float).
* @param dis_size Length of dis.
*/
KRL_IMPRECISE_FUNCTION_BEGIN
int krl_L2sqr(const float *x, const float *__restrict y, const size_t d, float *dis, size_t dis_size)
{
  constexpr size_t single_round = 4;
  constexpr size_t multi_round = 16;
  size_t i;
  float res;

  if (d < 1 || d > 65535) {
    std::printf("Error: INVALPARAM in krl_L2sqr\n");
    return INVALPARAM;
  }

  if (x == nullptr || y == nullptr || dis == nullptr || dis_size < 1) {
    std::printf("Error: INVALPOINTER in krl_L2sqr\n");
    return INVALPOINTER;
  }

  if (likely(d >= multi_round)) {
    prefetch_Lx(x + multi_round);
    prefetch_Lx(y + multi_round);
    float32x4_t x8_0 = vld1q_f32(x);
    float32x4_t x8_1 = vld1q_f32(x + 4);
    float32x4_t x8_2 = vld1q_f32(x + 8);
    float32x4_t x8_3 = vld1q_f32(x + 12);

    float32x4_t y8_0 = vld1q_f32(y);
    float32x4_t y8_1 = vld1q_f32(y + 4);
    float32x4_t y8_2 = vld1q_f32(y + 8);
    float32x4_t y8_3 = vld1q_f32(y + 12);

    float32x4_t d8_0 = vsubq_f32(x8_0, y8_0);
    d8_0 = vmulq_f32(d8_0, d8_0);
    float32x4_t d8_1 = vsubq_f32(x8_1, y8_1);
    d8_1 = vmulq_f32(d8_1, d8_1);
    float32x4_t d8_2 = vsubq_f32(x8_2, y8_2);
    d8_2 = vmulq_f32(d8_2, d8_2);
    float32x4_t d8_3 = vsubq_f32(x8_3, y8_3);
    d8_3 = vmulq_f32(d8_3, d8_3);

    for (i = multi_round; i <= d - multi_round; i += multi_round) {
      prefetch_Lx(x + i + multi_round);
      prefetch_Lx(y + i + multi_round);
      x8_0 = vld1q_f32(x + i);
      y8_0 = vld1q_f32(y + i);
      const float32x4_t q8_0 = vsubq_f32(x8_0, y8_0);
      d8_0 = vmlaq_f32(d8_0, q8_0, q8_0);

      x8_1 = vld1q_f32(x + i + 4);
      y8_1 = vld1q_f32(y + i + 4);
      const float32x4_t q8_1 = vsubq_f32(x8_1, y8_1);
      d8_1 = vmlaq_f32(d8_1, q8_1, q8_1);

      x8_2 = vld1q_f32(x + i + 8);
      y8_2 = vld1q_f32(y + i + 8);
      const float32x4_t q8_2 = vsubq_f32(x8_2, y8_2);
      d8_2 = vmlaq_f32(d8_2, q8_2, q8_2);

      x8_3 = vld1q_f32(x + i + 12);
      y8_3 = vld1q_f32(y + i + 12);
      const float32x4_t q8_3 = vsubq_f32(x8_3, y8_3);
      d8_3 = vmlaq_f32(d8_3, q8_3, q8_3);
    }

    for (; i <= d - single_round; i += single_round) {
      x8_0 = vld1q_f32(x + i);
      y8_0 = vld1q_f32(y + i);
      const float32x4_t q8_0 = vsubq_f32(x8_0, y8_0);
      d8_0 = vmlaq_f32(d8_0, q8_0, q8_0);
    }

    d8_0 = vaddq_f32(d8_0, d8_1);
    d8_2 = vaddq_f32(d8_2, d8_3);
    d8_0 = vaddq_f32(d8_0, d8_2);
    res = vaddvq_f32(d8_0);
  } else if (d >= single_round) {
    float32x4_t x8_0 = vld1q_f32(x);
    float32x4_t y8_0 = vld1q_f32(y);

    float32x4_t d8_0 = vsubq_f32(x8_0, y8_0);
    d8_0 = vmulq_f32(d8_0, d8_0);
    for (i = single_round; i <= d - single_round; i += single_round) {
      x8_0 = vld1q_f32(x + i);
      y8_0 = vld1q_f32(y + i);
      const float32x4_t q8_0 = vsubq_f32(x8_0, y8_0);
      d8_0 = vmlaq_f32(d8_0, q8_0, q8_0);
    }
    res = vaddvq_f32(d8_0);
  } else {
    res = 0;
    i = 0;
  }

  for (; i < d; i++) {
    const float tmp = x[i] - y[i];
    res += tmp * tmp;
  }
  *dis = res;
  return SUCCESS;
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute L2 squares for two float vectors in batch.
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Output array to store the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_L2sqr_idx_batch2(
  const float *x, const float *__restrict y0, const float *__restrict y1, const size_t d, float *dis)
{
  size_t i;
  constexpr size_t single_round = 4;
  constexpr size_t multi_round = 8;

  if (likely(d >= multi_round)) {
    float32x4_t x_0 = vld1q_f32(x);
    float32x4_t x_1 = vld1q_f32(x + 4);

    float32x4_t y0_0 = vld1q_f32(y0);
    float32x4_t y0_1 = vld1q_f32(y0 + 4);
    float32x4_t y1_0 = vld1q_f32(y1);
    float32x4_t y1_1 = vld1q_f32(y1 + 4);

    float32x4_t d0_0 = vsubq_f32(x_0, y0_0);
    d0_0 = vmulq_f32(d0_0, d0_0);
    float32x4_t d0_1 = vsubq_f32(x_1, y0_1);
    d0_1 = vmulq_f32(d0_1, d0_1);
    float32x4_t d1_0 = vsubq_f32(x_0, y1_0);
    d1_0 = vmulq_f32(d1_0, d1_0);
    float32x4_t d1_1 = vsubq_f32(x_1, y1_1);
    d1_1 = vmulq_f32(d1_1, d1_1);

    for (i = multi_round; i <= d - multi_round; i += multi_round) {
      x_0 = vld1q_f32(x + i);
      y0_0 = vld1q_f32(y0 + i);
      y1_0 = vld1q_f32(y1 + i);
      const float32x4_t q0_0 = vsubq_f32(x_0, y0_0);
      const float32x4_t q1_0 = vsubq_f32(x_0, y1_0);
      d0_0 = vmlaq_f32(d0_0, q0_0, q0_0);
      d1_0 = vmlaq_f32(d1_0, q1_0, q1_0);

      x_1 = vld1q_f32(x + i + 4);
      y0_1 = vld1q_f32(y0 + i + 4);
      y1_1 = vld1q_f32(y1 + i + 4);
      const float32x4_t q0_1 = vsubq_f32(x_1, y0_1);
      const float32x4_t q1_1 = vsubq_f32(x_1, y1_1);
      d0_1 = vmlaq_f32(d0_1, q0_1, q0_1);
      d1_1 = vmlaq_f32(d1_1, q1_1, q1_1);
    }

    for (; i <= d - single_round; i += single_round) {
      x_0 = vld1q_f32(x + i);
      y0_0 = vld1q_f32(y0 + i);
      y1_0 = vld1q_f32(y1 + i);
      const float32x4_t q0_0 = vsubq_f32(x_0, y0_0);
      const float32x4_t q1_0 = vsubq_f32(x_0, y1_0);
      d0_0 = vmlaq_f32(d0_0, q0_0, q0_0);
      d1_0 = vmlaq_f32(d1_0, q1_0, q1_0);
    }

    d0_0 = vaddq_f32(d0_0, d0_1);
    d1_0 = vaddq_f32(d1_0, d1_1);
    dis[0] = vaddvq_f32(d0_0);
    dis[1] = vaddvq_f32(d1_0);
  } else if (d >= single_round) {
    float32x4_t x8_0 = vld1q_f32(x);
    float32x4_t y8_0 = vld1q_f32(y0);
    float32x4_t y8_1 = vld1q_f32(y1);

    float32x4_t d8_0 = vsubq_f32(x8_0, y8_0);
    d8_0 = vmulq_f32(d8_0, d8_0);
    float32x4_t d8_1 = vsubq_f32(x8_0, y8_1);
    d8_1 = vmulq_f32(d8_1, d8_1);
    for (i = single_round; i <= d - single_round; i += single_round) {
      x8_0 = vld1q_f32(x);
      y8_0 = vld1q_f32(y0);
      y8_1 = vld1q_f32(y1);

      float32x4_t q0 = vsubq_f32(x8_0, y8_0);
      d8_0 = vmlaq_f32(d8_0, q0, q0);
      float32x4_t q1 = vsubq_f32(x8_0, y8_1);
      d8_1 = vmlaq_f32(d8_1, q1, q1);
    }
    dis[0] = vaddvq_f32(d8_0);
    dis[1] = vaddvq_f32(d8_1);
  } else {
    dis[0] = 0;
    dis[1] = 0;
    i = 0;
  }

  for (; i < d; i++) {
    const float tmp0 = x[i] - y0[i];
    const float tmp1 = x[i] - y1[i];
    dis[0] += tmp0 * tmp0;
    dis[1] += tmp1 * tmp1;
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute L2 squares for four float vectors in batch.
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Output array to store the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_L2sqr_idx_batch4(const float *x, const float *__restrict *y, const size_t d, float *dis)
{
  constexpr size_t single_round = 4;
  size_t i;
  if (likely(d >= single_round)) {
    float32x4_t b = vld1q_f32(x);

    float32x4_t q0 = vld1q_f32(y[0]);
    float32x4_t q1 = vld1q_f32(y[1]);
    float32x4_t q2 = vld1q_f32(y[2]);
    float32x4_t q3 = vld1q_f32(y[3]);

    q0 = vsubq_f32(q0, b);
    q1 = vsubq_f32(q1, b);
    q2 = vsubq_f32(q2, b);
    q3 = vsubq_f32(q3, b);

    float32x4_t res0 = vmulq_f32(q0, q0);
    float32x4_t res1 = vmulq_f32(q1, q1);
    float32x4_t res2 = vmulq_f32(q2, q2);
    float32x4_t res3 = vmulq_f32(q3, q3);

    for (i = single_round; i <= d - single_round; i += single_round) {
      b = vld1q_f32(x + i);

      q0 = vld1q_f32(y[0] + i);
      q1 = vld1q_f32(y[1] + i);
      q2 = vld1q_f32(y[2] + i);
      q3 = vld1q_f32(y[3] + i);

      q0 = vsubq_f32(q0, b);
      q1 = vsubq_f32(q1, b);
      q2 = vsubq_f32(q2, b);
      q3 = vsubq_f32(q3, b);

      res0 = vmlaq_f32(res0, q0, q0);
      res1 = vmlaq_f32(res1, q1, q1);
      res2 = vmlaq_f32(res2, q2, q2);
      res3 = vmlaq_f32(res3, q3, q3);
    }
    dis[0] = vaddvq_f32(res0);
    dis[1] = vaddvq_f32(res1);
    dis[2] = vaddvq_f32(res2);
    dis[3] = vaddvq_f32(res3);
  } else {
    for (int i = 0; i < 4; i++) {
      dis[i] = 0.0f;
    }
    i = 0;
  }
  if (d > i) {
    float q0 = x[i] - *(y[0] + i);
    float q1 = x[i] - *(y[1] + i);
    float q2 = x[i] - *(y[2] + i);
    float q3 = x[i] - *(y[3] + i);
    float d0 = q0 * q0;
    float d1 = q1 * q1;
    float d2 = q2 * q2;
    float d3 = q3 * q3;
    for (i++; i < d; ++i) {
      float q0 = x[i] - *(y[0] + i);
      float q1 = x[i] - *(y[1] + i);
      float q2 = x[i] - *(y[2] + i);
      float q3 = x[i] - *(y[3] + i);
      d0 += q0 * q0;
      d1 += q1 * q1;
      d2 += q2 * q2;
      d3 += q3 * q3;
    }
    dis[0] += d0;
    dis[1] += d1;
    dis[2] += d2;
    dis[3] += d3;
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute L2 squares for eight float vectors in batch.
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Output array to store the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_L2sqr_idx_prefetch_batch8(const float *x, const float *__restrict *y, const size_t d, float *dis)
{
  size_t i;
  constexpr size_t single_round = 4; /* 128/32 */
  constexpr size_t multi_round = 16; /* 4 * single_round */
  if (likely(d >= multi_round)) {
    float32x4_t neon_res1 = vdupq_n_f32(0.0);
    float32x4_t neon_res2 = vdupq_n_f32(0.0);
    float32x4_t neon_res3 = vdupq_n_f32(0.0);
    float32x4_t neon_res4 = vdupq_n_f32(0.0);
    float32x4_t neon_res5 = vdupq_n_f32(0.0);
    float32x4_t neon_res6 = vdupq_n_f32(0.0);
    float32x4_t neon_res7 = vdupq_n_f32(0.0);
    float32x4_t neon_res8 = vdupq_n_f32(0.0);
    for (i = 0; i < d - multi_round; i += multi_round) {
      prefetch_L1(x + i + multi_round);
      prefetch_Lx(y[0] + i + multi_round);
      prefetch_Lx(y[1] + i + multi_round);
      prefetch_Lx(y[2] + i + multi_round);
      prefetch_Lx(y[3] + i + multi_round);
      prefetch_Lx(y[4] + i + multi_round);
      prefetch_Lx(y[5] + i + multi_round);
      prefetch_Lx(y[6] + i + multi_round);
      prefetch_Lx(y[7] + i + multi_round);
      for (size_t j = 0; j < multi_round; j += single_round) {
        const float32x4_t neon_query = vld1q_f32(x + i + j);
        float32x4_t neon_base1 = vld1q_f32(y[0] + i + j);
        float32x4_t neon_base2 = vld1q_f32(y[1] + i + j);
        float32x4_t neon_base3 = vld1q_f32(y[2] + i + j);
        float32x4_t neon_base4 = vld1q_f32(y[3] + i + j);
        float32x4_t neon_base5 = vld1q_f32(y[4] + i + j);
        float32x4_t neon_base6 = vld1q_f32(y[5] + i + j);
        float32x4_t neon_base7 = vld1q_f32(y[6] + i + j);
        float32x4_t neon_base8 = vld1q_f32(y[7] + i + j);

        neon_base1 = vsubq_f32(neon_base1, neon_query);
        neon_base2 = vsubq_f32(neon_base2, neon_query);
        neon_base3 = vsubq_f32(neon_base3, neon_query);
        neon_base4 = vsubq_f32(neon_base4, neon_query);
        neon_base5 = vsubq_f32(neon_base5, neon_query);
        neon_base6 = vsubq_f32(neon_base6, neon_query);
        neon_base7 = vsubq_f32(neon_base7, neon_query);
        neon_base8 = vsubq_f32(neon_base8, neon_query);

        neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_base1);
        neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_base2);
        neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_base3);
        neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_base4);
        neon_res5 = vmlaq_f32(neon_res5, neon_base5, neon_base5);
        neon_res6 = vmlaq_f32(neon_res6, neon_base6, neon_base6);
        neon_res7 = vmlaq_f32(neon_res7, neon_base7, neon_base7);
        neon_res8 = vmlaq_f32(neon_res8, neon_base8, neon_base8);
      }
    }
    for (; i <= d - single_round; i += single_round) {
      const float32x4_t neon_query = vld1q_f32(x + i);
      float32x4_t neon_base1 = vld1q_f32(y[0] + i);
      float32x4_t neon_base2 = vld1q_f32(y[1] + i);
      float32x4_t neon_base3 = vld1q_f32(y[2] + i);
      float32x4_t neon_base4 = vld1q_f32(y[3] + i);
      float32x4_t neon_base5 = vld1q_f32(y[4] + i);
      float32x4_t neon_base6 = vld1q_f32(y[5] + i);
      float32x4_t neon_base7 = vld1q_f32(y[6] + i);
      float32x4_t neon_base8 = vld1q_f32(y[7] + i);

      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_base5 = vsubq_f32(neon_base5, neon_query);
      neon_base6 = vsubq_f32(neon_base6, neon_query);
      neon_base7 = vsubq_f32(neon_base7, neon_query);
      neon_base8 = vsubq_f32(neon_base8, neon_query);

      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_base1);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_base2);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_base3);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_base4);
      neon_res5 = vmlaq_f32(neon_res5, neon_base5, neon_base5);
      neon_res6 = vmlaq_f32(neon_res6, neon_base6, neon_base6);
      neon_res7 = vmlaq_f32(neon_res7, neon_base7, neon_base7);
      neon_res8 = vmlaq_f32(neon_res8, neon_base8, neon_base8);
    }
    dis[0] = vaddvq_f32(neon_res1);
    dis[1] = vaddvq_f32(neon_res2);
    dis[2] = vaddvq_f32(neon_res3);
    dis[3] = vaddvq_f32(neon_res4);
    dis[4] = vaddvq_f32(neon_res5);
    dis[5] = vaddvq_f32(neon_res6);
    dis[6] = vaddvq_f32(neon_res7);
    dis[7] = vaddvq_f32(neon_res8);
  } else if (d >= single_round) {
    float32x4_t neon_query = vld1q_f32(x);

    float32x4_t neon_base1 = vld1q_f32(y[0]);
    float32x4_t neon_base2 = vld1q_f32(y[1]);
    float32x4_t neon_base3 = vld1q_f32(y[2]);
    float32x4_t neon_base4 = vld1q_f32(y[3]);
    float32x4_t neon_base5 = vld1q_f32(y[4]);
    float32x4_t neon_base6 = vld1q_f32(y[5]);
    float32x4_t neon_base7 = vld1q_f32(y[6]);
    float32x4_t neon_base8 = vld1q_f32(y[7]);

    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    neon_base5 = vsubq_f32(neon_base5, neon_query);
    neon_base6 = vsubq_f32(neon_base6, neon_query);
    neon_base7 = vsubq_f32(neon_base7, neon_query);
    neon_base8 = vsubq_f32(neon_base8, neon_query);

    float32x4_t neon_res1 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res2 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res3 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res4 = vmulq_f32(neon_base4, neon_base4);
    float32x4_t neon_res5 = vmulq_f32(neon_base5, neon_base5);
    float32x4_t neon_res6 = vmulq_f32(neon_base6, neon_base6);
    float32x4_t neon_res7 = vmulq_f32(neon_base7, neon_base7);
    float32x4_t neon_res8 = vmulq_f32(neon_base8, neon_base8);
    for (i = single_round; i <= d - single_round; i += single_round) {
      neon_query = vld1q_f32(x + i);
      neon_base1 = vld1q_f32(y[0] + i);
      neon_base2 = vld1q_f32(y[1] + i);
      neon_base3 = vld1q_f32(y[2] + i);
      neon_base4 = vld1q_f32(y[3] + i);
      neon_base5 = vld1q_f32(y[4] + i);
      neon_base6 = vld1q_f32(y[5] + i);
      neon_base7 = vld1q_f32(y[6] + i);
      neon_base8 = vld1q_f32(y[7] + i);

      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_base5 = vsubq_f32(neon_base5, neon_query);
      neon_base6 = vsubq_f32(neon_base6, neon_query);
      neon_base7 = vsubq_f32(neon_base7, neon_query);
      neon_base8 = vsubq_f32(neon_base8, neon_query);

      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_base1);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_base2);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_base3);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_base4);
      neon_res5 = vmlaq_f32(neon_res5, neon_base5, neon_base5);
      neon_res6 = vmlaq_f32(neon_res6, neon_base6, neon_base6);
      neon_res7 = vmlaq_f32(neon_res7, neon_base7, neon_base7);
      neon_res8 = vmlaq_f32(neon_res8, neon_base8, neon_base8);
    }
    dis[0] = vaddvq_f32(neon_res1);
    dis[1] = vaddvq_f32(neon_res2);
    dis[2] = vaddvq_f32(neon_res3);
    dis[3] = vaddvq_f32(neon_res4);
    dis[4] = vaddvq_f32(neon_res5);
    dis[5] = vaddvq_f32(neon_res6);
    dis[6] = vaddvq_f32(neon_res7);
    dis[7] = vaddvq_f32(neon_res8);
  } else {
    for (int i = 0; i < 8; i++) {
      dis[i] = 0.0f;
    }
    i = 0;
  }
  if (i < d) {
    float q0 = x[i] - *(y[0] + i);
    float q1 = x[i] - *(y[1] + i);
    float q2 = x[i] - *(y[2] + i);
    float q3 = x[i] - *(y[3] + i);
    float q4 = x[i] - *(y[4] + i);
    float q5 = x[i] - *(y[5] + i);
    float q6 = x[i] - *(y[6] + i);
    float q7 = x[i] - *(y[7] + i);
    float d0 = q0 * q0;
    float d1 = q1 * q1;
    float d2 = q2 * q2;
    float d3 = q3 * q3;
    float d4 = q4 * q4;
    float d5 = q5 * q5;
    float d6 = q6 * q6;
    float d7 = q7 * q7;
    for (i++; i < d; ++i) {
      q0 = x[i] - *(y[0] + i);
      q1 = x[i] - *(y[1] + i);
      q2 = x[i] - *(y[2] + i);
      q3 = x[i] - *(y[3] + i);
      q4 = x[i] - *(y[4] + i);
      q5 = x[i] - *(y[5] + i);
      q6 = x[i] - *(y[6] + i);
      q7 = x[i] - *(y[7] + i);
      d0 += q0 * q0;
      d1 += q1 * q1;
      d2 += q2 * q2;
      d3 += q3 * q3;
      d4 += q4 * q4;
      d5 += q5 * q5;
      d6 += q6 * q6;
      d7 += q7 * q7;
    }
    dis[0] += d0;
    dis[1] += d1;
    dis[2] += d2;
    dis[3] += d3;
    dis[4] += d4;
    dis[5] += d5;
    dis[6] += d6;
    dis[7] += d7;
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute L2 squares for sixteen float vectors in batch.
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Output array to store the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_L2sqr_idx_prefetch_batch16(const float *x, const float *__restrict *y, const size_t d, float *dis)
{
  size_t i;
  constexpr size_t single_round = 4; /* 128/32 */
  constexpr size_t multi_round = 16; /* 2 * single_round */
  if (likely(d >= multi_round)) {
    float32x4_t neon_res1 = vdupq_n_f32(0.0);
    float32x4_t neon_res2 = vdupq_n_f32(0.0);
    float32x4_t neon_res3 = vdupq_n_f32(0.0);
    float32x4_t neon_res4 = vdupq_n_f32(0.0);
    float32x4_t neon_res5 = vdupq_n_f32(0.0);
    float32x4_t neon_res6 = vdupq_n_f32(0.0);
    float32x4_t neon_res7 = vdupq_n_f32(0.0);
    float32x4_t neon_res8 = vdupq_n_f32(0.0);
    float32x4_t neon_res9 = vdupq_n_f32(0.0);
    float32x4_t neon_res10 = vdupq_n_f32(0.0);
    float32x4_t neon_res11 = vdupq_n_f32(0.0);
    float32x4_t neon_res12 = vdupq_n_f32(0.0);
    float32x4_t neon_res13 = vdupq_n_f32(0.0);
    float32x4_t neon_res14 = vdupq_n_f32(0.0);
    float32x4_t neon_res15 = vdupq_n_f32(0.0);
    float32x4_t neon_res16 = vdupq_n_f32(0.0);
    for (i = 0; i < d - multi_round; i += multi_round) {
      prefetch_L1(x + i + multi_round);
      prefetch_Lx(y[0] + i + multi_round);
      prefetch_Lx(y[1] + i + multi_round);
      prefetch_Lx(y[2] + i + multi_round);
      prefetch_Lx(y[3] + i + multi_round);
      prefetch_Lx(y[4] + i + multi_round);
      prefetch_Lx(y[5] + i + multi_round);
      prefetch_Lx(y[6] + i + multi_round);
      prefetch_Lx(y[7] + i + multi_round);
      prefetch_Lx(y[8] + i + multi_round);
      prefetch_Lx(y[9] + i + multi_round);
      prefetch_Lx(y[10] + i + multi_round);
      prefetch_Lx(y[11] + i + multi_round);
      prefetch_Lx(y[12] + i + multi_round);
      prefetch_Lx(y[13] + i + multi_round);
      prefetch_Lx(y[14] + i + multi_round);
      prefetch_Lx(y[15] + i + multi_round);
      for (size_t j = 0; j < multi_round; j += single_round) {
        const float32x4_t neon_query = vld1q_f32(x + i + j);
        float32x4_t neon_base1 = vld1q_f32(y[0] + i + j);
        float32x4_t neon_base2 = vld1q_f32(y[1] + i + j);
        float32x4_t neon_base3 = vld1q_f32(y[2] + i + j);
        float32x4_t neon_base4 = vld1q_f32(y[3] + i + j);
        float32x4_t neon_base5 = vld1q_f32(y[4] + i + j);
        float32x4_t neon_base6 = vld1q_f32(y[5] + i + j);
        float32x4_t neon_base7 = vld1q_f32(y[6] + i + j);
        float32x4_t neon_base8 = vld1q_f32(y[7] + i + j);

        neon_base1 = vsubq_f32(neon_base1, neon_query);
        neon_base2 = vsubq_f32(neon_base2, neon_query);
        neon_base3 = vsubq_f32(neon_base3, neon_query);
        neon_base4 = vsubq_f32(neon_base4, neon_query);
        neon_base5 = vsubq_f32(neon_base5, neon_query);
        neon_base6 = vsubq_f32(neon_base6, neon_query);
        neon_base7 = vsubq_f32(neon_base7, neon_query);
        neon_base8 = vsubq_f32(neon_base8, neon_query);

        neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_base1);
        neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_base2);
        neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_base3);
        neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_base4);
        neon_res5 = vmlaq_f32(neon_res5, neon_base5, neon_base5);
        neon_res6 = vmlaq_f32(neon_res6, neon_base6, neon_base6);
        neon_res7 = vmlaq_f32(neon_res7, neon_base7, neon_base7);
        neon_res8 = vmlaq_f32(neon_res8, neon_base8, neon_base8);

        neon_base1 = vld1q_f32(y[8] + i + j);
        neon_base2 = vld1q_f32(y[9] + i + j);
        neon_base3 = vld1q_f32(y[10] + i + j);
        neon_base4 = vld1q_f32(y[11] + i + j);
        neon_base5 = vld1q_f32(y[12] + i + j);
        neon_base6 = vld1q_f32(y[13] + i + j);
        neon_base7 = vld1q_f32(y[14] + i + j);
        neon_base8 = vld1q_f32(y[15] + i + j);

        neon_base1 = vsubq_f32(neon_base1, neon_query);
        neon_base2 = vsubq_f32(neon_base2, neon_query);
        neon_base3 = vsubq_f32(neon_base3, neon_query);
        neon_base4 = vsubq_f32(neon_base4, neon_query);
        neon_base5 = vsubq_f32(neon_base5, neon_query);
        neon_base6 = vsubq_f32(neon_base6, neon_query);
        neon_base7 = vsubq_f32(neon_base7, neon_query);
        neon_base8 = vsubq_f32(neon_base8, neon_query);

        neon_res9 = vmlaq_f32(neon_res9, neon_base1, neon_base1);
        neon_res10 = vmlaq_f32(neon_res10, neon_base2, neon_base2);
        neon_res11 = vmlaq_f32(neon_res11, neon_base3, neon_base3);
        neon_res12 = vmlaq_f32(neon_res12, neon_base4, neon_base4);
        neon_res13 = vmlaq_f32(neon_res13, neon_base5, neon_base5);
        neon_res14 = vmlaq_f32(neon_res14, neon_base6, neon_base6);
        neon_res15 = vmlaq_f32(neon_res15, neon_base7, neon_base7);
        neon_res16 = vmlaq_f32(neon_res16, neon_base8, neon_base8);
      }
    }
    for (; i <= d - single_round; i += single_round) {
      const float32x4_t neon_query = vld1q_f32(x + i);
      float32x4_t neon_base1 = vld1q_f32(y[0] + i);
      float32x4_t neon_base2 = vld1q_f32(y[1] + i);
      float32x4_t neon_base3 = vld1q_f32(y[2] + i);
      float32x4_t neon_base4 = vld1q_f32(y[3] + i);
      float32x4_t neon_base5 = vld1q_f32(y[4] + i);
      float32x4_t neon_base6 = vld1q_f32(y[5] + i);
      float32x4_t neon_base7 = vld1q_f32(y[6] + i);
      float32x4_t neon_base8 = vld1q_f32(y[7] + i);

      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_base5 = vsubq_f32(neon_base5, neon_query);
      neon_base6 = vsubq_f32(neon_base6, neon_query);
      neon_base7 = vsubq_f32(neon_base7, neon_query);
      neon_base8 = vsubq_f32(neon_base8, neon_query);

      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_base1);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_base2);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_base3);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_base4);
      neon_res5 = vmlaq_f32(neon_res5, neon_base5, neon_base5);
      neon_res6 = vmlaq_f32(neon_res6, neon_base6, neon_base6);
      neon_res7 = vmlaq_f32(neon_res7, neon_base7, neon_base7);
      neon_res8 = vmlaq_f32(neon_res8, neon_base8, neon_base8);

      neon_base1 = vld1q_f32(y[8] + i);
      neon_base2 = vld1q_f32(y[9] + i);
      neon_base3 = vld1q_f32(y[10] + i);
      neon_base4 = vld1q_f32(y[11] + i);
      neon_base5 = vld1q_f32(y[12] + i);
      neon_base6 = vld1q_f32(y[13] + i);
      neon_base7 = vld1q_f32(y[14] + i);
      neon_base8 = vld1q_f32(y[15] + i);

      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_base5 = vsubq_f32(neon_base5, neon_query);
      neon_base6 = vsubq_f32(neon_base6, neon_query);
      neon_base7 = vsubq_f32(neon_base7, neon_query);
      neon_base8 = vsubq_f32(neon_base8, neon_query);

      neon_res9 = vmlaq_f32(neon_res9, neon_base1, neon_base1);
      neon_res10 = vmlaq_f32(neon_res10, neon_base2, neon_base2);
      neon_res11 = vmlaq_f32(neon_res11, neon_base3, neon_base3);
      neon_res12 = vmlaq_f32(neon_res12, neon_base4, neon_base4);
      neon_res13 = vmlaq_f32(neon_res13, neon_base5, neon_base5);
      neon_res14 = vmlaq_f32(neon_res14, neon_base6, neon_base6);
      neon_res15 = vmlaq_f32(neon_res15, neon_base7, neon_base7);
      neon_res16 = vmlaq_f32(neon_res16, neon_base8, neon_base8);
    }
    dis[0] = vaddvq_f32(neon_res1);
    dis[1] = vaddvq_f32(neon_res2);
    dis[2] = vaddvq_f32(neon_res3);
    dis[3] = vaddvq_f32(neon_res4);
    dis[4] = vaddvq_f32(neon_res5);
    dis[5] = vaddvq_f32(neon_res6);
    dis[6] = vaddvq_f32(neon_res7);
    dis[7] = vaddvq_f32(neon_res8);
    dis[8] = vaddvq_f32(neon_res9);
    dis[9] = vaddvq_f32(neon_res10);
    dis[10] = vaddvq_f32(neon_res11);
    dis[11] = vaddvq_f32(neon_res12);
    dis[12] = vaddvq_f32(neon_res13);
    dis[13] = vaddvq_f32(neon_res14);
    dis[14] = vaddvq_f32(neon_res15);
    dis[15] = vaddvq_f32(neon_res16);
  } else if (d >= single_round) {
    float32x4_t neon_query = vld1q_f32(x);

    float32x4_t neon_base1 = vld1q_f32(y[0]);
    float32x4_t neon_base2 = vld1q_f32(y[1]);
    float32x4_t neon_base3 = vld1q_f32(y[2]);
    float32x4_t neon_base4 = vld1q_f32(y[3]);
    float32x4_t neon_base5 = vld1q_f32(y[4]);
    float32x4_t neon_base6 = vld1q_f32(y[5]);
    float32x4_t neon_base7 = vld1q_f32(y[6]);
    float32x4_t neon_base8 = vld1q_f32(y[7]);

    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    neon_base5 = vsubq_f32(neon_base5, neon_query);
    neon_base6 = vsubq_f32(neon_base6, neon_query);
    neon_base7 = vsubq_f32(neon_base7, neon_query);
    neon_base8 = vsubq_f32(neon_base8, neon_query);

    float32x4_t neon_res1 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res2 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res3 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res4 = vmulq_f32(neon_base4, neon_base4);
    float32x4_t neon_res5 = vmulq_f32(neon_base5, neon_base5);
    float32x4_t neon_res6 = vmulq_f32(neon_base6, neon_base6);
    float32x4_t neon_res7 = vmulq_f32(neon_base7, neon_base7);
    float32x4_t neon_res8 = vmulq_f32(neon_base8, neon_base8);

    neon_base1 = vld1q_f32(y[8]);
    neon_base2 = vld1q_f32(y[9]);
    neon_base3 = vld1q_f32(y[10]);
    neon_base4 = vld1q_f32(y[11]);
    neon_base5 = vld1q_f32(y[12]);
    neon_base6 = vld1q_f32(y[13]);
    neon_base7 = vld1q_f32(y[14]);
    neon_base8 = vld1q_f32(y[15]);

    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    neon_base5 = vsubq_f32(neon_base5, neon_query);
    neon_base6 = vsubq_f32(neon_base6, neon_query);
    neon_base7 = vsubq_f32(neon_base7, neon_query);
    neon_base8 = vsubq_f32(neon_base8, neon_query);

    float32x4_t neon_res9 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res10 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res11 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res12 = vmulq_f32(neon_base4, neon_base4);
    float32x4_t neon_res13 = vmulq_f32(neon_base5, neon_base5);
    float32x4_t neon_res14 = vmulq_f32(neon_base6, neon_base6);
    float32x4_t neon_res15 = vmulq_f32(neon_base7, neon_base7);
    float32x4_t neon_res16 = vmulq_f32(neon_base8, neon_base8);
    for (i = single_round; i <= d - single_round; i += single_round) {
      neon_query = vld1q_f32(x + i);
      neon_base1 = vld1q_f32(y[0] + i);
      neon_base2 = vld1q_f32(y[1] + i);
      neon_base3 = vld1q_f32(y[2] + i);
      neon_base4 = vld1q_f32(y[3] + i);
      neon_base5 = vld1q_f32(y[4] + i);
      neon_base6 = vld1q_f32(y[5] + i);
      neon_base7 = vld1q_f32(y[6] + i);
      neon_base8 = vld1q_f32(y[7] + i);

      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_base5 = vsubq_f32(neon_base5, neon_query);
      neon_base6 = vsubq_f32(neon_base6, neon_query);
      neon_base7 = vsubq_f32(neon_base7, neon_query);
      neon_base8 = vsubq_f32(neon_base8, neon_query);

      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_base1);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_base2);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_base3);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_base4);
      neon_res5 = vmlaq_f32(neon_res5, neon_base5, neon_base5);
      neon_res6 = vmlaq_f32(neon_res6, neon_base6, neon_base6);
      neon_res7 = vmlaq_f32(neon_res7, neon_base7, neon_base7);
      neon_res8 = vmlaq_f32(neon_res8, neon_base8, neon_base8);

      neon_base1 = vld1q_f32(y[8] + i);
      neon_base2 = vld1q_f32(y[9] + i);
      neon_base3 = vld1q_f32(y[10] + i);
      neon_base4 = vld1q_f32(y[11] + i);
      neon_base5 = vld1q_f32(y[12] + i);
      neon_base6 = vld1q_f32(y[13] + i);
      neon_base7 = vld1q_f32(y[14] + i);
      neon_base8 = vld1q_f32(y[15] + i);

      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_base5 = vsubq_f32(neon_base5, neon_query);
      neon_base6 = vsubq_f32(neon_base6, neon_query);
      neon_base7 = vsubq_f32(neon_base7, neon_query);
      neon_base8 = vsubq_f32(neon_base8, neon_query);

      neon_res9 = vmlaq_f32(neon_res9, neon_base1, neon_base1);
      neon_res10 = vmlaq_f32(neon_res10, neon_base2, neon_base2);
      neon_res11 = vmlaq_f32(neon_res11, neon_base3, neon_base3);
      neon_res12 = vmlaq_f32(neon_res12, neon_base4, neon_base4);
      neon_res13 = vmlaq_f32(neon_res13, neon_base5, neon_base5);
      neon_res14 = vmlaq_f32(neon_res14, neon_base6, neon_base6);
      neon_res15 = vmlaq_f32(neon_res15, neon_base7, neon_base7);
      neon_res16 = vmlaq_f32(neon_res16, neon_base8, neon_base8);
    }
    dis[0] = vaddvq_f32(neon_res1);
    dis[1] = vaddvq_f32(neon_res2);
    dis[2] = vaddvq_f32(neon_res3);
    dis[3] = vaddvq_f32(neon_res4);
    dis[4] = vaddvq_f32(neon_res5);
    dis[5] = vaddvq_f32(neon_res6);
    dis[6] = vaddvq_f32(neon_res7);
    dis[7] = vaddvq_f32(neon_res8);
    dis[8] = vaddvq_f32(neon_res9);
    dis[9] = vaddvq_f32(neon_res10);
    dis[10] = vaddvq_f32(neon_res11);
    dis[11] = vaddvq_f32(neon_res12);
    dis[12] = vaddvq_f32(neon_res13);
    dis[13] = vaddvq_f32(neon_res14);
    dis[14] = vaddvq_f32(neon_res15);
    dis[15] = vaddvq_f32(neon_res16);
  } else {
    for (int i = 0; i < 16; i++) {
      dis[i] = 0.0f;
    }
    i = 0;
  }
  if (i < d) {
    float q0 = x[i] - *(y[0] + i);
    float q1 = x[i] - *(y[1] + i);
    float q2 = x[i] - *(y[2] + i);
    float q3 = x[i] - *(y[3] + i);
    float q4 = x[i] - *(y[4] + i);
    float q5 = x[i] - *(y[5] + i);
    float q6 = x[i] - *(y[6] + i);
    float q7 = x[i] - *(y[7] + i);
    float d0 = q0 * q0;
    float d1 = q1 * q1;
    float d2 = q2 * q2;
    float d3 = q3 * q3;
    float d4 = q4 * q4;
    float d5 = q5 * q5;
    float d6 = q6 * q6;
    float d7 = q7 * q7;
    q0 = x[i] - *(y[8] + i);
    q1 = x[i] - *(y[9] + i);
    q2 = x[i] - *(y[10] + i);
    q3 = x[i] - *(y[11] + i);
    q4 = x[i] - *(y[12] + i);
    q5 = x[i] - *(y[13] + i);
    q6 = x[i] - *(y[14] + i);
    q7 = x[i] - *(y[15] + i);
    float d8 = q0 * q0;
    float d9 = q1 * q1;
    float d10 = q2 * q2;
    float d11 = q3 * q3;
    float d12 = q4 * q4;
    float d13 = q5 * q5;
    float d14 = q6 * q6;
    float d15 = q7 * q7;
    for (i++; i < d; ++i) {
      q0 = x[i] - *(y[0] + i);
      q1 = x[i] - *(y[1] + i);
      q2 = x[i] - *(y[2] + i);
      q3 = x[i] - *(y[3] + i);
      q4 = x[i] - *(y[4] + i);
      q5 = x[i] - *(y[5] + i);
      q6 = x[i] - *(y[6] + i);
      q7 = x[i] - *(y[7] + i);
      d0 += q0 * q0;
      d1 += q1 * q1;
      d2 += q2 * q2;
      d3 += q3 * q3;
      d4 += q4 * q4;
      d5 += q5 * q5;
      d6 += q6 * q6;
      d7 += q7 * q7;
      q0 = x[i] - *(y[8] + i);
      q1 = x[i] - *(y[9] + i);
      q2 = x[i] - *(y[10] + i);
      q3 = x[i] - *(y[11] + i);
      q4 = x[i] - *(y[12] + i);
      q5 = x[i] - *(y[13] + i);
      q6 = x[i] - *(y[14] + i);
      q7 = x[i] - *(y[15] + i);
      d8 += q0 * q0;
      d9 += q1 * q1;
      d10 += q2 * q2;
      d11 += q3 * q3;
      d12 += q4 * q4;
      d13 += q5 * q5;
      d14 += q6 * q6;
      d15 += q7 * q7;
    }
    dis[0] += d0;
    dis[1] += d1;
    dis[2] += d2;
    dis[3] += d3;
    dis[4] += d4;
    dis[5] += d5;
    dis[6] += d6;
    dis[7] += d7;
    dis[8] += d8;
    dis[9] += d9;
    dis[10] += d10;
    dis[11] += d11;
    dis[12] += d12;
    dis[13] += d13;
    dis[14] += d14;
    dis[15] += d15;
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute L2 squares for twenty-four float vectors in batch.
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Output array to store the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_L2sqr_idx_prefetch_batch24(const float *x, const float *__restrict *y, const size_t d, float *dis)
{
  size_t i;
  constexpr size_t single_round = 4; /* 128 / 32 */
  constexpr size_t multi_round = 16; /* 2 * single_round */
  if (likely(d >= multi_round)) {
    prefetch_L1(x + multi_round);
    prefetch_Lx(y[0] + multi_round);
    prefetch_Lx(y[1] + multi_round);
    prefetch_Lx(y[2] + multi_round);
    prefetch_Lx(y[3] + multi_round);
    prefetch_Lx(y[4] + multi_round);
    prefetch_Lx(y[5] + multi_round);
    prefetch_Lx(y[6] + multi_round);
    prefetch_Lx(y[7] + multi_round);
    prefetch_Lx(y[8] + multi_round);
    prefetch_Lx(y[9] + multi_round);
    prefetch_Lx(y[10] + multi_round);
    prefetch_Lx(y[11] + multi_round);
    prefetch_Lx(y[12] + multi_round);
    prefetch_Lx(y[13] + multi_round);
    prefetch_Lx(y[14] + multi_round);
    prefetch_Lx(y[15] + multi_round);
    prefetch_Lx(y[16] + multi_round);
    prefetch_Lx(y[17] + multi_round);
    prefetch_Lx(y[18] + multi_round);
    prefetch_Lx(y[19] + multi_round);
    prefetch_Lx(y[20] + multi_round);
    prefetch_Lx(y[21] + multi_round);
    prefetch_Lx(y[22] + multi_round);
    prefetch_Lx(y[23] + multi_round);
    float32x4_t neon_res1, neon_res2, neon_res3, neon_res4;
    float32x4_t neon_res5, neon_res6, neon_res7, neon_res8;
    float32x4_t neon_res9, neon_res10, neon_res11, neon_res12;
    float32x4_t neon_res13, neon_res14, neon_res15, neon_res16;
    float32x4_t neon_res17, neon_res18, neon_res19, neon_res20;
    float32x4_t neon_res21, neon_res22, neon_res23, neon_res24;
    {
      const float32x4_t neon_query = vld1q_f32(x);
      float32x4_t neon_base1 = vld1q_f32(y[0]);
      float32x4_t neon_base2 = vld1q_f32(y[1]);
      float32x4_t neon_base3 = vld1q_f32(y[2]);
      float32x4_t neon_base4 = vld1q_f32(y[3]);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res1 = vmulq_f32(neon_base1, neon_base1);
      neon_res2 = vmulq_f32(neon_base2, neon_base2);
      neon_res3 = vmulq_f32(neon_base3, neon_base3);
      neon_res4 = vmulq_f32(neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[4]);
      neon_base2 = vld1q_f32(y[5]);
      neon_base3 = vld1q_f32(y[6]);
      neon_base4 = vld1q_f32(y[7]);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res5 = vmulq_f32(neon_base1, neon_base1);
      neon_res6 = vmulq_f32(neon_base2, neon_base2);
      neon_res7 = vmulq_f32(neon_base3, neon_base3);
      neon_res8 = vmulq_f32(neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[8]);
      neon_base2 = vld1q_f32(y[9]);
      neon_base3 = vld1q_f32(y[10]);
      neon_base4 = vld1q_f32(y[11]);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res9 = vmulq_f32(neon_base1, neon_base1);
      neon_res10 = vmulq_f32(neon_base2, neon_base2);
      neon_res11 = vmulq_f32(neon_base3, neon_base3);
      neon_res12 = vmulq_f32(neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[12]);
      neon_base2 = vld1q_f32(y[13]);
      neon_base3 = vld1q_f32(y[14]);
      neon_base4 = vld1q_f32(y[15]);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res13 = vmulq_f32(neon_base1, neon_base1);
      neon_res14 = vmulq_f32(neon_base2, neon_base2);
      neon_res15 = vmulq_f32(neon_base3, neon_base3);
      neon_res16 = vmulq_f32(neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[16]);
      neon_base2 = vld1q_f32(y[17]);
      neon_base3 = vld1q_f32(y[18]);
      neon_base4 = vld1q_f32(y[19]);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res17 = vmulq_f32(neon_base1, neon_base1);
      neon_res18 = vmulq_f32(neon_base2, neon_base2);
      neon_res19 = vmulq_f32(neon_base3, neon_base3);
      neon_res20 = vmulq_f32(neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[20]);
      neon_base2 = vld1q_f32(y[21]);
      neon_base3 = vld1q_f32(y[22]);
      neon_base4 = vld1q_f32(y[23]);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res21 = vmulq_f32(neon_base1, neon_base1);
      neon_res22 = vmulq_f32(neon_base2, neon_base2);
      neon_res23 = vmulq_f32(neon_base3, neon_base3);
      neon_res24 = vmulq_f32(neon_base4, neon_base4);
    }
    for (i = single_round; i < multi_round; i += single_round) {
      const float32x4_t neon_query = vld1q_f32(x + i);
      float32x4_t neon_base1 = vld1q_f32(y[0] + i);
      float32x4_t neon_base2 = vld1q_f32(y[1] + i);
      float32x4_t neon_base3 = vld1q_f32(y[2] + i);
      float32x4_t neon_base4 = vld1q_f32(y[3] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_base1);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_base2);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_base3);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[4] + i);
      neon_base2 = vld1q_f32(y[5] + i);
      neon_base3 = vld1q_f32(y[6] + i);
      neon_base4 = vld1q_f32(y[7] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res5 = vmlaq_f32(neon_res5, neon_base1, neon_base1);
      neon_res6 = vmlaq_f32(neon_res6, neon_base2, neon_base2);
      neon_res7 = vmlaq_f32(neon_res7, neon_base3, neon_base3);
      neon_res8 = vmlaq_f32(neon_res8, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[8] + i);
      neon_base2 = vld1q_f32(y[9] + i);
      neon_base3 = vld1q_f32(y[10] + i);
      neon_base4 = vld1q_f32(y[11] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res9 = vmlaq_f32(neon_res9, neon_base1, neon_base1);
      neon_res10 = vmlaq_f32(neon_res10, neon_base2, neon_base2);
      neon_res11 = vmlaq_f32(neon_res11, neon_base3, neon_base3);
      neon_res12 = vmlaq_f32(neon_res12, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[12] + i);
      neon_base2 = vld1q_f32(y[13] + i);
      neon_base3 = vld1q_f32(y[14] + i);
      neon_base4 = vld1q_f32(y[15] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res13 = vmlaq_f32(neon_res13, neon_base1, neon_base1);
      neon_res14 = vmlaq_f32(neon_res14, neon_base2, neon_base2);
      neon_res15 = vmlaq_f32(neon_res15, neon_base3, neon_base3);
      neon_res16 = vmlaq_f32(neon_res16, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[16] + i);
      neon_base2 = vld1q_f32(y[17] + i);
      neon_base3 = vld1q_f32(y[18] + i);
      neon_base4 = vld1q_f32(y[19] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res17 = vmlaq_f32(neon_res17, neon_base1, neon_base1);
      neon_res18 = vmlaq_f32(neon_res18, neon_base2, neon_base2);
      neon_res19 = vmlaq_f32(neon_res19, neon_base3, neon_base3);
      neon_res20 = vmlaq_f32(neon_res20, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[20] + i);
      neon_base2 = vld1q_f32(y[21] + i);
      neon_base3 = vld1q_f32(y[22] + i);
      neon_base4 = vld1q_f32(y[23] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res21 = vmlaq_f32(neon_res21, neon_base1, neon_base1);
      neon_res22 = vmlaq_f32(neon_res22, neon_base2, neon_base2);
      neon_res23 = vmlaq_f32(neon_res23, neon_base3, neon_base3);
      neon_res24 = vmlaq_f32(neon_res24, neon_base4, neon_base4);
    }
    for (; i < d - multi_round; i += multi_round) {
      prefetch_L1(x + i + multi_round);
      prefetch_Lx(y[0] + i + multi_round);
      prefetch_Lx(y[1] + i + multi_round);
      prefetch_Lx(y[2] + i + multi_round);
      prefetch_Lx(y[3] + i + multi_round);
      prefetch_Lx(y[4] + i + multi_round);
      prefetch_Lx(y[5] + i + multi_round);
      prefetch_Lx(y[6] + i + multi_round);
      prefetch_Lx(y[7] + i + multi_round);
      prefetch_Lx(y[8] + i + multi_round);
      prefetch_Lx(y[9] + i + multi_round);
      prefetch_Lx(y[10] + i + multi_round);
      prefetch_Lx(y[11] + i + multi_round);
      prefetch_Lx(y[12] + i + multi_round);
      prefetch_Lx(y[13] + i + multi_round);
      prefetch_Lx(y[14] + i + multi_round);
      prefetch_Lx(y[15] + i + multi_round);
      prefetch_Lx(y[16] + i + multi_round);
      prefetch_Lx(y[17] + i + multi_round);
      prefetch_Lx(y[18] + i + multi_round);
      prefetch_Lx(y[19] + i + multi_round);
      prefetch_Lx(y[20] + i + multi_round);
      prefetch_Lx(y[21] + i + multi_round);
      prefetch_Lx(y[22] + i + multi_round);
      prefetch_Lx(y[23] + i + multi_round);
      for (size_t j = i; j < i + multi_round; j += single_round) {
        const float32x4_t neon_query = vld1q_f32(x + j);
        float32x4_t neon_base1 = vld1q_f32(y[0] + j);
        float32x4_t neon_base2 = vld1q_f32(y[1] + j);
        float32x4_t neon_base3 = vld1q_f32(y[2] + j);
        float32x4_t neon_base4 = vld1q_f32(y[3] + j);
        neon_base1 = vsubq_f32(neon_base1, neon_query);
        neon_base2 = vsubq_f32(neon_base2, neon_query);
        neon_base3 = vsubq_f32(neon_base3, neon_query);
        neon_base4 = vsubq_f32(neon_base4, neon_query);
        neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_base1);
        neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_base2);
        neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_base3);
        neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_base4);

        neon_base1 = vld1q_f32(y[4] + j);
        neon_base2 = vld1q_f32(y[5] + j);
        neon_base3 = vld1q_f32(y[6] + j);
        neon_base4 = vld1q_f32(y[7] + j);
        neon_base1 = vsubq_f32(neon_base1, neon_query);
        neon_base2 = vsubq_f32(neon_base2, neon_query);
        neon_base3 = vsubq_f32(neon_base3, neon_query);
        neon_base4 = vsubq_f32(neon_base4, neon_query);
        neon_res5 = vmlaq_f32(neon_res5, neon_base1, neon_base1);
        neon_res6 = vmlaq_f32(neon_res6, neon_base2, neon_base2);
        neon_res7 = vmlaq_f32(neon_res7, neon_base3, neon_base3);
        neon_res8 = vmlaq_f32(neon_res8, neon_base4, neon_base4);

        neon_base1 = vld1q_f32(y[8] + j);
        neon_base2 = vld1q_f32(y[9] + j);
        neon_base3 = vld1q_f32(y[10] + j);
        neon_base4 = vld1q_f32(y[11] + j);
        neon_base1 = vsubq_f32(neon_base1, neon_query);
        neon_base2 = vsubq_f32(neon_base2, neon_query);
        neon_base3 = vsubq_f32(neon_base3, neon_query);
        neon_base4 = vsubq_f32(neon_base4, neon_query);
        neon_res9 = vmlaq_f32(neon_res9, neon_base1, neon_base1);
        neon_res10 = vmlaq_f32(neon_res10, neon_base2, neon_base2);
        neon_res11 = vmlaq_f32(neon_res11, neon_base3, neon_base3);
        neon_res12 = vmlaq_f32(neon_res12, neon_base4, neon_base4);

        neon_base1 = vld1q_f32(y[12] + j);
        neon_base2 = vld1q_f32(y[13] + j);
        neon_base3 = vld1q_f32(y[14] + j);
        neon_base4 = vld1q_f32(y[15] + j);
        neon_base1 = vsubq_f32(neon_base1, neon_query);
        neon_base2 = vsubq_f32(neon_base2, neon_query);
        neon_base3 = vsubq_f32(neon_base3, neon_query);
        neon_base4 = vsubq_f32(neon_base4, neon_query);
        neon_res13 = vmlaq_f32(neon_res13, neon_base1, neon_base1);
        neon_res14 = vmlaq_f32(neon_res14, neon_base2, neon_base2);
        neon_res15 = vmlaq_f32(neon_res15, neon_base3, neon_base3);
        neon_res16 = vmlaq_f32(neon_res16, neon_base4, neon_base4);

        neon_base1 = vld1q_f32(y[16] + j);
        neon_base2 = vld1q_f32(y[17] + j);
        neon_base3 = vld1q_f32(y[18] + j);
        neon_base4 = vld1q_f32(y[19] + j);
        neon_base1 = vsubq_f32(neon_base1, neon_query);
        neon_base2 = vsubq_f32(neon_base2, neon_query);
        neon_base3 = vsubq_f32(neon_base3, neon_query);
        neon_base4 = vsubq_f32(neon_base4, neon_query);
        neon_res17 = vmlaq_f32(neon_res17, neon_base1, neon_base1);
        neon_res18 = vmlaq_f32(neon_res18, neon_base2, neon_base2);
        neon_res19 = vmlaq_f32(neon_res19, neon_base3, neon_base3);
        neon_res20 = vmlaq_f32(neon_res20, neon_base4, neon_base4);

        neon_base1 = vld1q_f32(y[20] + j);
        neon_base2 = vld1q_f32(y[21] + j);
        neon_base3 = vld1q_f32(y[22] + j);
        neon_base4 = vld1q_f32(y[23] + j);
        neon_base1 = vsubq_f32(neon_base1, neon_query);
        neon_base2 = vsubq_f32(neon_base2, neon_query);
        neon_base3 = vsubq_f32(neon_base3, neon_query);
        neon_base4 = vsubq_f32(neon_base4, neon_query);
        neon_res21 = vmlaq_f32(neon_res21, neon_base1, neon_base1);
        neon_res22 = vmlaq_f32(neon_res22, neon_base2, neon_base2);
        neon_res23 = vmlaq_f32(neon_res23, neon_base3, neon_base3);
        neon_res24 = vmlaq_f32(neon_res24, neon_base4, neon_base4);
      }
    }
    for (; i <= d - single_round; i += single_round) {
      const float32x4_t neon_query = vld1q_f32(x + i);
      float32x4_t neon_base1 = vld1q_f32(y[0] + i);
      float32x4_t neon_base2 = vld1q_f32(y[1] + i);
      float32x4_t neon_base3 = vld1q_f32(y[2] + i);
      float32x4_t neon_base4 = vld1q_f32(y[3] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_base1);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_base2);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_base3);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[4] + i);
      neon_base2 = vld1q_f32(y[5] + i);
      neon_base3 = vld1q_f32(y[6] + i);
      neon_base4 = vld1q_f32(y[7] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res5 = vmlaq_f32(neon_res5, neon_base1, neon_base1);
      neon_res6 = vmlaq_f32(neon_res6, neon_base2, neon_base2);
      neon_res7 = vmlaq_f32(neon_res7, neon_base3, neon_base3);
      neon_res8 = vmlaq_f32(neon_res8, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[8] + i);
      neon_base2 = vld1q_f32(y[9] + i);
      neon_base3 = vld1q_f32(y[10] + i);
      neon_base4 = vld1q_f32(y[11] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res9 = vmlaq_f32(neon_res9, neon_base1, neon_base1);
      neon_res10 = vmlaq_f32(neon_res10, neon_base2, neon_base2);
      neon_res11 = vmlaq_f32(neon_res11, neon_base3, neon_base3);
      neon_res12 = vmlaq_f32(neon_res12, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[12] + i);
      neon_base2 = vld1q_f32(y[13] + i);
      neon_base3 = vld1q_f32(y[14] + i);
      neon_base4 = vld1q_f32(y[15] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res13 = vmlaq_f32(neon_res13, neon_base1, neon_base1);
      neon_res14 = vmlaq_f32(neon_res14, neon_base2, neon_base2);
      neon_res15 = vmlaq_f32(neon_res15, neon_base3, neon_base3);
      neon_res16 = vmlaq_f32(neon_res16, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[16] + i);
      neon_base2 = vld1q_f32(y[17] + i);
      neon_base3 = vld1q_f32(y[18] + i);
      neon_base4 = vld1q_f32(y[19] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res17 = vmlaq_f32(neon_res17, neon_base1, neon_base1);
      neon_res18 = vmlaq_f32(neon_res18, neon_base2, neon_base2);
      neon_res19 = vmlaq_f32(neon_res19, neon_base3, neon_base3);
      neon_res20 = vmlaq_f32(neon_res20, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[20] + i);
      neon_base2 = vld1q_f32(y[21] + i);
      neon_base3 = vld1q_f32(y[22] + i);
      neon_base4 = vld1q_f32(y[23] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res21 = vmlaq_f32(neon_res21, neon_base1, neon_base1);
      neon_res22 = vmlaq_f32(neon_res22, neon_base2, neon_base2);
      neon_res23 = vmlaq_f32(neon_res23, neon_base3, neon_base3);
      neon_res24 = vmlaq_f32(neon_res24, neon_base4, neon_base4);
    }
    dis[0] = vaddvq_f32(neon_res1);
    dis[1] = vaddvq_f32(neon_res2);
    dis[2] = vaddvq_f32(neon_res3);
    dis[3] = vaddvq_f32(neon_res4);
    dis[4] = vaddvq_f32(neon_res5);
    dis[5] = vaddvq_f32(neon_res6);
    dis[6] = vaddvq_f32(neon_res7);
    dis[7] = vaddvq_f32(neon_res8);
    dis[8] = vaddvq_f32(neon_res9);
    dis[9] = vaddvq_f32(neon_res10);
    dis[10] = vaddvq_f32(neon_res11);
    dis[11] = vaddvq_f32(neon_res12);
    dis[12] = vaddvq_f32(neon_res13);
    dis[13] = vaddvq_f32(neon_res14);
    dis[14] = vaddvq_f32(neon_res15);
    dis[15] = vaddvq_f32(neon_res16);
    dis[16] = vaddvq_f32(neon_res17);
    dis[17] = vaddvq_f32(neon_res18);
    dis[18] = vaddvq_f32(neon_res19);
    dis[19] = vaddvq_f32(neon_res20);
    dis[20] = vaddvq_f32(neon_res21);
    dis[21] = vaddvq_f32(neon_res22);
    dis[22] = vaddvq_f32(neon_res23);
    dis[23] = vaddvq_f32(neon_res24);
  } else if (d >= single_round) {
    float32x4_t neon_query = vld1q_f32(x);
    float32x4_t neon_base1 = vld1q_f32(y[0]);
    float32x4_t neon_base2 = vld1q_f32(y[1]);
    float32x4_t neon_base3 = vld1q_f32(y[2]);
    float32x4_t neon_base4 = vld1q_f32(y[3]);
    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    float32x4_t neon_res1 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res2 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res3 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res4 = vmulq_f32(neon_base4, neon_base4);

    neon_base1 = vld1q_f32(y[4]);
    neon_base2 = vld1q_f32(y[5]);
    neon_base3 = vld1q_f32(y[6]);
    neon_base4 = vld1q_f32(y[7]);
    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    float32x4_t neon_res5 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res6 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res7 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res8 = vmulq_f32(neon_base4, neon_base4);

    neon_base1 = vld1q_f32(y[8]);
    neon_base2 = vld1q_f32(y[9]);
    neon_base3 = vld1q_f32(y[10]);
    neon_base4 = vld1q_f32(y[11]);
    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    float32x4_t neon_res9 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res10 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res11 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res12 = vmulq_f32(neon_base4, neon_base4);

    neon_base1 = vld1q_f32(y[12]);
    neon_base2 = vld1q_f32(y[13]);
    neon_base3 = vld1q_f32(y[14]);
    neon_base4 = vld1q_f32(y[15]);
    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    float32x4_t neon_res13 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res14 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res15 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res16 = vmulq_f32(neon_base4, neon_base4);

    neon_base1 = vld1q_f32(y[16]);
    neon_base2 = vld1q_f32(y[17]);
    neon_base3 = vld1q_f32(y[18]);
    neon_base4 = vld1q_f32(y[19]);
    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    float32x4_t neon_res17 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res18 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res19 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res20 = vmulq_f32(neon_base4, neon_base4);

    neon_base1 = vld1q_f32(y[20]);
    neon_base2 = vld1q_f32(y[21]);
    neon_base3 = vld1q_f32(y[22]);
    neon_base4 = vld1q_f32(y[23]);
    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    float32x4_t neon_res21 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res22 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res23 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res24 = vmulq_f32(neon_base4, neon_base4);
    for (i = single_round; i <= d - single_round; i += single_round) {
      neon_query = vld1q_f32(x + i);
      neon_base1 = vld1q_f32(y[0] + i);
      neon_base2 = vld1q_f32(y[1] + i);
      neon_base3 = vld1q_f32(y[2] + i);
      neon_base4 = vld1q_f32(y[3] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_base1);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_base2);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_base3);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[4] + i);
      neon_base2 = vld1q_f32(y[5] + i);
      neon_base3 = vld1q_f32(y[6] + i);
      neon_base4 = vld1q_f32(y[7] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res5 = vmlaq_f32(neon_res5, neon_base1, neon_base1);
      neon_res6 = vmlaq_f32(neon_res6, neon_base2, neon_base2);
      neon_res7 = vmlaq_f32(neon_res7, neon_base3, neon_base3);
      neon_res8 = vmlaq_f32(neon_res8, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[8] + i);
      neon_base2 = vld1q_f32(y[9] + i);
      neon_base3 = vld1q_f32(y[10] + i);
      neon_base4 = vld1q_f32(y[11] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res9 = vmlaq_f32(neon_res9, neon_base1, neon_base1);
      neon_res10 = vmlaq_f32(neon_res10, neon_base2, neon_base2);
      neon_res11 = vmlaq_f32(neon_res11, neon_base3, neon_base3);
      neon_res12 = vmlaq_f32(neon_res12, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[12] + i);
      neon_base2 = vld1q_f32(y[13] + i);
      neon_base3 = vld1q_f32(y[14] + i);
      neon_base4 = vld1q_f32(y[15] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res13 = vmlaq_f32(neon_res13, neon_base1, neon_base1);
      neon_res14 = vmlaq_f32(neon_res14, neon_base2, neon_base2);
      neon_res15 = vmlaq_f32(neon_res15, neon_base3, neon_base3);
      neon_res16 = vmlaq_f32(neon_res16, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[16] + i);
      neon_base2 = vld1q_f32(y[17] + i);
      neon_base3 = vld1q_f32(y[18] + i);
      neon_base4 = vld1q_f32(y[19] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res17 = vmlaq_f32(neon_res17, neon_base1, neon_base1);
      neon_res18 = vmlaq_f32(neon_res18, neon_base2, neon_base2);
      neon_res19 = vmlaq_f32(neon_res19, neon_base3, neon_base3);
      neon_res20 = vmlaq_f32(neon_res20, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y[20] + i);
      neon_base2 = vld1q_f32(y[21] + i);
      neon_base3 = vld1q_f32(y[22] + i);
      neon_base4 = vld1q_f32(y[23] + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res21 = vmlaq_f32(neon_res21, neon_base1, neon_base1);
      neon_res22 = vmlaq_f32(neon_res22, neon_base2, neon_base2);
      neon_res23 = vmlaq_f32(neon_res23, neon_base3, neon_base3);
      neon_res24 = vmlaq_f32(neon_res24, neon_base4, neon_base4);
    }
    dis[0] = vaddvq_f32(neon_res1);
    dis[1] = vaddvq_f32(neon_res2);
    dis[2] = vaddvq_f32(neon_res3);
    dis[3] = vaddvq_f32(neon_res4);
    dis[4] = vaddvq_f32(neon_res5);
    dis[5] = vaddvq_f32(neon_res6);
    dis[6] = vaddvq_f32(neon_res7);
    dis[7] = vaddvq_f32(neon_res8);
    dis[8] = vaddvq_f32(neon_res9);
    dis[9] = vaddvq_f32(neon_res10);
    dis[10] = vaddvq_f32(neon_res11);
    dis[11] = vaddvq_f32(neon_res12);
    dis[12] = vaddvq_f32(neon_res13);
    dis[13] = vaddvq_f32(neon_res14);
    dis[14] = vaddvq_f32(neon_res15);
    dis[15] = vaddvq_f32(neon_res16);
    dis[16] = vaddvq_f32(neon_res17);
    dis[17] = vaddvq_f32(neon_res18);
    dis[18] = vaddvq_f32(neon_res19);
    dis[19] = vaddvq_f32(neon_res20);
    dis[20] = vaddvq_f32(neon_res21);
    dis[21] = vaddvq_f32(neon_res22);
    dis[22] = vaddvq_f32(neon_res23);
    dis[23] = vaddvq_f32(neon_res24);
  } else {
    for (int i = 0; i < 24; i++) {
      dis[i] = 0.0f;
    }
    i = 0;
  }
  if (i < d) {
    float q0 = x[i] - *(y[0] + i);
    float q1 = x[i] - *(y[1] + i);
    float q2 = x[i] - *(y[2] + i);
    float q3 = x[i] - *(y[3] + i);
    float q4 = x[i] - *(y[4] + i);
    float q5 = x[i] - *(y[5] + i);
    float q6 = x[i] - *(y[6] + i);
    float q7 = x[i] - *(y[7] + i);
    float d0 = q0 * q0;
    float d1 = q1 * q1;
    float d2 = q2 * q2;
    float d3 = q3 * q3;
    float d4 = q4 * q4;
    float d5 = q5 * q5;
    float d6 = q6 * q6;
    float d7 = q7 * q7;
    q0 = x[i] - *(y[8] + i);
    q1 = x[i] - *(y[9] + i);
    q2 = x[i] - *(y[10] + i);
    q3 = x[i] - *(y[11] + i);
    q4 = x[i] - *(y[12] + i);
    q5 = x[i] - *(y[13] + i);
    q6 = x[i] - *(y[14] + i);
    q7 = x[i] - *(y[15] + i);
    float d8 = q0 * q0;
    float d9 = q1 * q1;
    float d10 = q2 * q2;
    float d11 = q3 * q3;
    float d12 = q4 * q4;
    float d13 = q5 * q5;
    float d14 = q6 * q6;
    float d15 = q7 * q7;
    q0 = x[i] - *(y[16] + i);
    q1 = x[i] - *(y[17] + i);
    q2 = x[i] - *(y[18] + i);
    q3 = x[i] - *(y[19] + i);
    q4 = x[i] - *(y[20] + i);
    q5 = x[i] - *(y[21] + i);
    q6 = x[i] - *(y[22] + i);
    q7 = x[i] - *(y[23] + i);
    float d16 = q0 * q0;
    float d17 = q1 * q1;
    float d18 = q2 * q2;
    float d19 = q3 * q3;
    float d20 = q4 * q4;
    float d21 = q5 * q5;
    float d22 = q6 * q6;
    float d23 = q7 * q7;
    for (i++; i < d; ++i) {
      q0 = x[i] - *(y[0] + i);
      q1 = x[i] - *(y[1] + i);
      q2 = x[i] - *(y[2] + i);
      q3 = x[i] - *(y[3] + i);
      q4 = x[i] - *(y[4] + i);
      q5 = x[i] - *(y[5] + i);
      q6 = x[i] - *(y[6] + i);
      q7 = x[i] - *(y[7] + i);
      d0 += q0 * q0;
      d1 += q1 * q1;
      d2 += q2 * q2;
      d3 += q3 * q3;
      d4 += q4 * q4;
      d5 += q5 * q5;
      d6 += q6 * q6;
      d7 += q7 * q7;
      q0 = x[i] - *(y[8] + i);
      q1 = x[i] - *(y[9] + i);
      q2 = x[i] - *(y[10] + i);
      q3 = x[i] - *(y[11] + i);
      q4 = x[i] - *(y[12] + i);
      q5 = x[i] - *(y[13] + i);
      q6 = x[i] - *(y[14] + i);
      q7 = x[i] - *(y[15] + i);
      d8 += q0 * q0;
      d9 += q1 * q1;
      d10 += q2 * q2;
      d11 += q3 * q3;
      d12 += q4 * q4;
      d13 += q5 * q5;
      d14 += q6 * q6;
      d15 += q7 * q7;
      q0 = x[i] - *(y[16] + i);
      q1 = x[i] - *(y[17] + i);
      q2 = x[i] - *(y[18] + i);
      q3 = x[i] - *(y[19] + i);
      q4 = x[i] - *(y[20] + i);
      q5 = x[i] - *(y[21] + i);
      q6 = x[i] - *(y[22] + i);
      q7 = x[i] - *(y[23] + i);
      d16 += q0 * q0;
      d17 += q1 * q1;
      d18 += q2 * q2;
      d19 += q3 * q3;
      d20 += q4 * q4;
      d21 += q5 * q5;
      d22 += q6 * q6;
      d23 += q7 * q7;
    }
    dis[0] += d0;
    dis[1] += d1;
    dis[2] += d2;
    dis[3] += d3;
    dis[4] += d4;
    dis[5] += d5;
    dis[6] += d6;
    dis[7] += d7;
    dis[8] += d8;
    dis[9] += d9;
    dis[10] += d10;
    dis[11] += d11;
    dis[12] += d12;
    dis[13] += d13;
    dis[14] += d14;
    dis[15] += d15;
    dis[16] += d16;
    dis[17] += d17;
    dis[18] += d18;
    dis[19] += d19;
    dis[20] += d20;
    dis[21] += d21;
    dis[22] += d22;
    dis[23] += d23;
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute L2 square distance for two vectors in batch mode.
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Pointer to the output array for storing the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_L2sqr_batch2(const float *x, const float *__restrict y, const size_t d, float *dis)
{
  size_t i;
  constexpr size_t single_round = 8;

  if (likely(d >= single_round)) {
    float32x4_t x_0 = vld1q_f32(x);
    float32x4_t x_1 = vld1q_f32(x + 4);

    float32x4_t y0_0 = vld1q_f32(y);
    float32x4_t y0_1 = vld1q_f32(y + 4);
    float32x4_t y1_0 = vld1q_f32(y + d);
    float32x4_t y1_1 = vld1q_f32(y + d + 4);

    float32x4_t d0_0 = vsubq_f32(x_0, y0_0);
    d0_0 = vmulq_f32(d0_0, d0_0);
    float32x4_t d0_1 = vsubq_f32(x_1, y0_1);
    d0_1 = vmulq_f32(d0_1, d0_1);
    float32x4_t d1_0 = vsubq_f32(x_0, y1_0);
    d1_0 = vmulq_f32(d1_0, d1_0);
    float32x4_t d1_1 = vsubq_f32(x_1, y1_1);
    d1_1 = vmulq_f32(d1_1, d1_1);

    for (i = single_round; i <= d - single_round; i += single_round) {
      x_0 = vld1q_f32(x + i);
      y0_0 = vld1q_f32(y + i);
      y1_0 = vld1q_f32(y + d + i);
      const float32x4_t q0_0 = vsubq_f32(x_0, y0_0);
      const float32x4_t q1_0 = vsubq_f32(x_0, y1_0);
      d0_0 = vmlaq_f32(d0_0, q0_0, q0_0);
      d1_0 = vmlaq_f32(d1_0, q1_0, q1_0);

      x_1 = vld1q_f32(x + i + 4);
      y0_1 = vld1q_f32(y + i + 4);
      y1_1 = vld1q_f32(y + d + i + 4);
      const float32x4_t q0_1 = vsubq_f32(x_1, y0_1);
      const float32x4_t q1_1 = vsubq_f32(x_1, y1_1);
      d0_1 = vmlaq_f32(d0_1, q0_1, q0_1);
      d1_1 = vmlaq_f32(d1_1, q1_1, q1_1);
    }

    d0_0 = vaddq_f32(d0_0, d0_1);
    d1_0 = vaddq_f32(d1_0, d1_1);
    dis[0] = vaddvq_f32(d0_0);
    dis[1] = vaddvq_f32(d1_0);
  } else {
    dis[0] = 0;
    dis[1] = 0;
    i = 0;
  }

  for (; i < d; i++) {
    const float tmp0 = x[i] - *(y + i);
    const float tmp1 = x[i] - *(y + d + i);
    dis[0] += tmp0 * tmp0;
    dis[1] += tmp1 * tmp1;
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute L2 square distance for four vectors in batch mode.
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Pointer to the output array for storing the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_L2sqr_batch4(const float *x, const float *__restrict y, const size_t d, float *dis)
{
  constexpr size_t single_round = 4;
  size_t i;
  if (likely(d >= single_round)) {
    float32x4_t b = vld1q_f32(x);

    float32x4_t q0 = vld1q_f32(y);
    float32x4_t q1 = vld1q_f32(y + d);
    float32x4_t q2 = vld1q_f32(y + 2 * d);
    float32x4_t q3 = vld1q_f32(y + 3 * d);

    q0 = vsubq_f32(q0, b);
    q1 = vsubq_f32(q1, b);
    q2 = vsubq_f32(q2, b);
    q3 = vsubq_f32(q3, b);

    float32x4_t res0 = vmulq_f32(q0, q0);
    float32x4_t res1 = vmulq_f32(q1, q1);
    float32x4_t res2 = vmulq_f32(q2, q2);
    float32x4_t res3 = vmulq_f32(q3, q3);

    for (i = single_round; i <= d - single_round; i += single_round) {
      b = vld1q_f32(x + i);

      q0 = vld1q_f32(y + i);
      q1 = vld1q_f32(y + d + i);
      q2 = vld1q_f32(y + 2 * d + i);
      q3 = vld1q_f32(y + 3 * d + i);

      q0 = vsubq_f32(q0, b);
      q1 = vsubq_f32(q1, b);
      q2 = vsubq_f32(q2, b);
      q3 = vsubq_f32(q3, b);

      res0 = vmlaq_f32(res0, q0, q0);
      res1 = vmlaq_f32(res1, q1, q1);
      res2 = vmlaq_f32(res2, q2, q2);
      res3 = vmlaq_f32(res3, q3, q3);
    }
    dis[0] = vaddvq_f32(res0);
    dis[1] = vaddvq_f32(res1);
    dis[2] = vaddvq_f32(res2);
    dis[3] = vaddvq_f32(res3);
  } else {
    for (int i = 0; i < 4; i++) {
      dis[i] = 0.0f;
    }
    i = 0;
  }
  if (d > i) {
    float q0 = x[i] - *(y + i);
    float q1 = x[i] - *(y + d + i);
    float q2 = x[i] - *(y + 2 * d + i);
    float q3 = x[i] - *(y + 3 * d + i);
    float d0 = q0 * q0;
    float d1 = q1 * q1;
    float d2 = q2 * q2;
    float d3 = q3 * q3;
    for (i++; i < d; ++i) {
      float q0 = x[i] - *(y + i);
      float q1 = x[i] - *(y + d + i);
      float q2 = x[i] - *(y + 2 * d + i);
      float q3 = x[i] - *(y + 3 * d + i);
      d0 += q0 * q0;
      d1 += q1 * q1;
      d2 += q2 * q2;
      d3 += q3 * q3;
    }
    dis[0] += d0;
    dis[1] += d1;
    dis[2] += d2;
    dis[3] += d3;
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute L2 square distance for eight vectors in batch mode.
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Pointer to the output array for storing the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_L2sqr_batch8(const float *x, const float *__restrict y, const size_t d, float *dis)
{
  size_t i;
  constexpr size_t single_round = 4;
  if (likely(d >= single_round)) {
    float32x4_t neon_query = vld1q_f32(x);

    float32x4_t neon_base1 = vld1q_f32(y);
    float32x4_t neon_base2 = vld1q_f32(y + d);
    float32x4_t neon_base3 = vld1q_f32(y + 2 * d);
    float32x4_t neon_base4 = vld1q_f32(y + 3 * d);
    float32x4_t neon_base5 = vld1q_f32(y + 4 * d);
    float32x4_t neon_base6 = vld1q_f32(y + 5 * d);
    float32x4_t neon_base7 = vld1q_f32(y + 6 * d);
    float32x4_t neon_base8 = vld1q_f32(y + 7 * d);

    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    neon_base5 = vsubq_f32(neon_base5, neon_query);
    neon_base6 = vsubq_f32(neon_base6, neon_query);
    neon_base7 = vsubq_f32(neon_base7, neon_query);
    neon_base8 = vsubq_f32(neon_base8, neon_query);

    float32x4_t neon_res1 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res2 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res3 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res4 = vmulq_f32(neon_base4, neon_base4);
    float32x4_t neon_res5 = vmulq_f32(neon_base5, neon_base5);
    float32x4_t neon_res6 = vmulq_f32(neon_base6, neon_base6);
    float32x4_t neon_res7 = vmulq_f32(neon_base7, neon_base7);
    float32x4_t neon_res8 = vmulq_f32(neon_base8, neon_base8);

    for (i = single_round; i <= d - single_round; i += single_round) {
      neon_query = vld1q_f32(x + i);

      neon_base1 = vld1q_f32(y + i);
      neon_base2 = vld1q_f32(y + d + i);
      neon_base3 = vld1q_f32(y + 2 * d + i);
      neon_base4 = vld1q_f32(y + 3 * d + i);
      neon_base5 = vld1q_f32(y + 4 * d + i);
      neon_base6 = vld1q_f32(y + 5 * d + i);
      neon_base7 = vld1q_f32(y + 6 * d + i);
      neon_base8 = vld1q_f32(y + 7 * d + i);

      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_base5 = vsubq_f32(neon_base5, neon_query);
      neon_base6 = vsubq_f32(neon_base6, neon_query);
      neon_base7 = vsubq_f32(neon_base7, neon_query);
      neon_base8 = vsubq_f32(neon_base8, neon_query);

      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_base1);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_base2);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_base3);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_base4);
      neon_res5 = vmlaq_f32(neon_res5, neon_base5, neon_base5);
      neon_res6 = vmlaq_f32(neon_res6, neon_base6, neon_base6);
      neon_res7 = vmlaq_f32(neon_res7, neon_base7, neon_base7);
      neon_res8 = vmlaq_f32(neon_res8, neon_base8, neon_base8);
    }
    dis[0] = vaddvq_f32(neon_res1);
    dis[1] = vaddvq_f32(neon_res2);
    dis[2] = vaddvq_f32(neon_res3);
    dis[3] = vaddvq_f32(neon_res4);
    dis[4] = vaddvq_f32(neon_res5);
    dis[5] = vaddvq_f32(neon_res6);
    dis[6] = vaddvq_f32(neon_res7);
    dis[7] = vaddvq_f32(neon_res8);
  } else {
    for (int i = 0; i < 8; i++) {
      dis[i] = 0.0f;
    }
    i = 0;
  }
  if (i < d) {
    float q0 = x[i] - *(y + i);
    float q1 = x[i] - *(y + d + i);
    float q2 = x[i] - *(y + 2 * d + i);
    float q3 = x[i] - *(y + 3 * d + i);
    float q4 = x[i] - *(y + 4 * d + i);
    float q5 = x[i] - *(y + 5 * d + i);
    float q6 = x[i] - *(y + 6 * d + i);
    float q7 = x[i] - *(y + 7 * d + i);
    float d0 = q0 * q0;
    float d1 = q1 * q1;
    float d2 = q2 * q2;
    float d3 = q3 * q3;
    float d4 = q4 * q4;
    float d5 = q5 * q5;
    float d6 = q6 * q6;
    float d7 = q7 * q7;
    for (i++; i < d; ++i) {
      q0 = x[i] - *(y + i);
      q1 = x[i] - *(y + d + i);
      q2 = x[i] - *(y + 2 * d + i);
      q3 = x[i] - *(y + 3 * d + i);
      q4 = x[i] - *(y + 4 * d + i);
      q5 = x[i] - *(y + 5 * d + i);
      q6 = x[i] - *(y + 6 * d + i);
      q7 = x[i] - *(y + 7 * d + i);
      d0 += q0 * q0;
      d1 += q1 * q1;
      d2 += q2 * q2;
      d3 += q3 * q3;
      d4 += q4 * q4;
      d5 += q5 * q5;
      d6 += q6 * q6;
      d7 += q7 * q7;
    }
    dis[0] += d0;
    dis[1] += d1;
    dis[2] += d2;
    dis[3] += d3;
    dis[4] += d4;
    dis[5] += d5;
    dis[6] += d6;
    dis[7] += d7;
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute L2 square distance for sixteen vectors in batch mode.
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Pointer to the output array for storing the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_L2sqr_batch16(const float *x, const float *__restrict y, const size_t d, float *dis)
{
  size_t i;
  constexpr size_t single_round = 4; /* 128 / 32 */
  if (likely(d >= single_round)) {
    float32x4_t neon_query = vld1q_f32(x);

    float32x4_t neon_base1 = vld1q_f32(y);
    float32x4_t neon_base2 = vld1q_f32(y + d);
    float32x4_t neon_base3 = vld1q_f32(y + 2 * d);
    float32x4_t neon_base4 = vld1q_f32(y + 3 * d);
    float32x4_t neon_base5 = vld1q_f32(y + 4 * d);
    float32x4_t neon_base6 = vld1q_f32(y + 5 * d);
    float32x4_t neon_base7 = vld1q_f32(y + 6 * d);
    float32x4_t neon_base8 = vld1q_f32(y + 7 * d);

    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    neon_base5 = vsubq_f32(neon_base5, neon_query);
    neon_base6 = vsubq_f32(neon_base6, neon_query);
    neon_base7 = vsubq_f32(neon_base7, neon_query);
    neon_base8 = vsubq_f32(neon_base8, neon_query);

    float32x4_t neon_res1 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res2 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res3 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res4 = vmulq_f32(neon_base4, neon_base4);
    float32x4_t neon_res5 = vmulq_f32(neon_base5, neon_base5);
    float32x4_t neon_res6 = vmulq_f32(neon_base6, neon_base6);
    float32x4_t neon_res7 = vmulq_f32(neon_base7, neon_base7);
    float32x4_t neon_res8 = vmulq_f32(neon_base8, neon_base8);

    neon_base1 = vld1q_f32(y + 8 * d);
    neon_base2 = vld1q_f32(y + 9 * d);
    neon_base3 = vld1q_f32(y + 10 * d);
    neon_base4 = vld1q_f32(y + 11 * d);
    neon_base5 = vld1q_f32(y + 12 * d);
    neon_base6 = vld1q_f32(y + 13 * d);
    neon_base7 = vld1q_f32(y + 14 * d);
    neon_base8 = vld1q_f32(y + 15 * d);

    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    neon_base5 = vsubq_f32(neon_base5, neon_query);
    neon_base6 = vsubq_f32(neon_base6, neon_query);
    neon_base7 = vsubq_f32(neon_base7, neon_query);
    neon_base8 = vsubq_f32(neon_base8, neon_query);

    float32x4_t neon_res9 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res10 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res11 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res12 = vmulq_f32(neon_base4, neon_base4);
    float32x4_t neon_res13 = vmulq_f32(neon_base5, neon_base5);
    float32x4_t neon_res14 = vmulq_f32(neon_base6, neon_base6);
    float32x4_t neon_res15 = vmulq_f32(neon_base7, neon_base7);
    float32x4_t neon_res16 = vmulq_f32(neon_base8, neon_base8);

    for (i = single_round; i <= d - single_round; i += single_round) {
      neon_query = vld1q_f32(x + i);
      neon_base1 = vld1q_f32(y + i);
      neon_base2 = vld1q_f32(y + d + i);
      neon_base3 = vld1q_f32(y + 2 * d + i);
      neon_base4 = vld1q_f32(y + 3 * d + i);
      neon_base5 = vld1q_f32(y + 4 * d + i);
      neon_base6 = vld1q_f32(y + 5 * d + i);
      neon_base7 = vld1q_f32(y + 6 * d + i);
      neon_base8 = vld1q_f32(y + 7 * d + i);

      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_base5 = vsubq_f32(neon_base5, neon_query);
      neon_base6 = vsubq_f32(neon_base6, neon_query);
      neon_base7 = vsubq_f32(neon_base7, neon_query);
      neon_base8 = vsubq_f32(neon_base8, neon_query);

      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_base1);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_base2);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_base3);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_base4);
      neon_res5 = vmlaq_f32(neon_res5, neon_base5, neon_base5);
      neon_res6 = vmlaq_f32(neon_res6, neon_base6, neon_base6);
      neon_res7 = vmlaq_f32(neon_res7, neon_base7, neon_base7);
      neon_res8 = vmlaq_f32(neon_res8, neon_base8, neon_base8);

      neon_base1 = vld1q_f32(y + 8 * d + i);
      neon_base2 = vld1q_f32(y + 9 * d + i);
      neon_base3 = vld1q_f32(y + 10 * d + i);
      neon_base4 = vld1q_f32(y + 11 * d + i);
      neon_base5 = vld1q_f32(y + 12 * d + i);
      neon_base6 = vld1q_f32(y + 13 * d + i);
      neon_base7 = vld1q_f32(y + 14 * d + i);
      neon_base8 = vld1q_f32(y + 15 * d + i);

      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_base5 = vsubq_f32(neon_base5, neon_query);
      neon_base6 = vsubq_f32(neon_base6, neon_query);
      neon_base7 = vsubq_f32(neon_base7, neon_query);
      neon_base8 = vsubq_f32(neon_base8, neon_query);

      neon_res9 = vmlaq_f32(neon_res9, neon_base1, neon_base1);
      neon_res10 = vmlaq_f32(neon_res10, neon_base2, neon_base2);
      neon_res11 = vmlaq_f32(neon_res11, neon_base3, neon_base3);
      neon_res12 = vmlaq_f32(neon_res12, neon_base4, neon_base4);
      neon_res13 = vmlaq_f32(neon_res13, neon_base5, neon_base5);
      neon_res14 = vmlaq_f32(neon_res14, neon_base6, neon_base6);
      neon_res15 = vmlaq_f32(neon_res15, neon_base7, neon_base7);
      neon_res16 = vmlaq_f32(neon_res16, neon_base8, neon_base8);
    }
    dis[0] = vaddvq_f32(neon_res1);
    dis[1] = vaddvq_f32(neon_res2);
    dis[2] = vaddvq_f32(neon_res3);
    dis[3] = vaddvq_f32(neon_res4);
    dis[4] = vaddvq_f32(neon_res5);
    dis[5] = vaddvq_f32(neon_res6);
    dis[6] = vaddvq_f32(neon_res7);
    dis[7] = vaddvq_f32(neon_res8);
    dis[8] = vaddvq_f32(neon_res9);
    dis[9] = vaddvq_f32(neon_res10);
    dis[10] = vaddvq_f32(neon_res11);
    dis[11] = vaddvq_f32(neon_res12);
    dis[12] = vaddvq_f32(neon_res13);
    dis[13] = vaddvq_f32(neon_res14);
    dis[14] = vaddvq_f32(neon_res15);
    dis[15] = vaddvq_f32(neon_res16);
  } else {
    for (int i = 0; i < 16; i++) {
      dis[i] = 0.0f;
    }
    i = 0;
  }
  if (i < d) {
    float q0 = x[i] - *(y + i);
    float q1 = x[i] - *(y + d + i);
    float q2 = x[i] - *(y + 2 * d + i);
    float q3 = x[i] - *(y + 3 * d + i);
    float q4 = x[i] - *(y + 4 * d + i);
    float q5 = x[i] - *(y + 5 * d + i);
    float q6 = x[i] - *(y + 6 * d + i);
    float q7 = x[i] - *(y + 7 * d + i);
    float d0 = q0 * q0;
    float d1 = q1 * q1;
    float d2 = q2 * q2;
    float d3 = q3 * q3;
    float d4 = q4 * q4;
    float d5 = q5 * q5;
    float d6 = q6 * q6;
    float d7 = q7 * q7;
    q0 = x[i] - *(y + 8 * d + i);
    q1 = x[i] - *(y + 9 * d + i);
    q2 = x[i] - *(y + 10 * d + i);
    q3 = x[i] - *(y + 11 * d + i);
    q4 = x[i] - *(y + 12 * d + i);
    q5 = x[i] - *(y + 13 * d + i);
    q6 = x[i] - *(y + 14 * d + i);
    q7 = x[i] - *(y + 15 * d + i);
    float d8 = q0 * q0;
    float d9 = q1 * q1;
    float d10 = q2 * q2;
    float d11 = q3 * q3;
    float d12 = q4 * q4;
    float d13 = q5 * q5;
    float d14 = q6 * q6;
    float d15 = q7 * q7;
    for (i++; i < d; ++i) {
      q0 = x[i] - *(y + i);
      q1 = x[i] - *(y + d + i);
      q2 = x[i] - *(y + 2 * d + i);
      q3 = x[i] - *(y + 3 * d + i);
      q4 = x[i] - *(y + 4 * d + i);
      q5 = x[i] - *(y + 5 * d + i);
      q6 = x[i] - *(y + 6 * d + i);
      q7 = x[i] - *(y + 7 * d + i);
      d0 += q0 * q0;
      d1 += q1 * q1;
      d2 += q2 * q2;
      d3 += q3 * q3;
      d4 += q4 * q4;
      d5 += q5 * q5;
      d6 += q6 * q6;
      d7 += q7 * q7;
      q0 = x[i] - *(y + 8 * d + i);
      q1 = x[i] - *(y + 9 * d + i);
      q2 = x[i] - *(y + 10 * d + i);
      q3 = x[i] - *(y + 11 * d + i);
      q4 = x[i] - *(y + 12 * d + i);
      q5 = x[i] - *(y + 13 * d + i);
      q6 = x[i] - *(y + 14 * d + i);
      q7 = x[i] - *(y + 15 * d + i);
      d8 += q0 * q0;
      d9 += q1 * q1;
      d10 += q2 * q2;
      d11 += q3 * q3;
      d12 += q4 * q4;
      d13 += q5 * q5;
      d14 += q6 * q6;
      d15 += q7 * q7;
    }
    dis[0] += d0;
    dis[1] += d1;
    dis[2] += d2;
    dis[3] += d3;
    dis[4] += d4;
    dis[5] += d5;
    dis[6] += d6;
    dis[7] += d7;
    dis[8] += d8;
    dis[9] += d9;
    dis[10] += d10;
    dis[11] += d11;
    dis[12] += d12;
    dis[13] += d13;
    dis[14] += d14;
    dis[15] += d15;
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute L2 square distance for twenty-four vectors in batch mode.
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Pointer to the output array for storing the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_L2sqr_batch24(const float *x, const float *__restrict y, const size_t d, float *dis)
{
  size_t i;
  constexpr size_t single_round = 4; /* 128 / 32 */
  if (likely(d >= single_round)) {
    float32x4_t neon_query = vld1q_f32(x);
    float32x4_t neon_base1 = vld1q_f32(y);
    float32x4_t neon_base2 = vld1q_f32(y + d);
    float32x4_t neon_base3 = vld1q_f32(y + 2 * d);
    float32x4_t neon_base4 = vld1q_f32(y + 3 * d);
    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    float32x4_t neon_res1 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res2 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res3 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res4 = vmulq_f32(neon_base4, neon_base4);

    neon_base1 = vld1q_f32(y + 4 * d);
    neon_base2 = vld1q_f32(y + 5 * d);
    neon_base3 = vld1q_f32(y + 6 * d);
    neon_base4 = vld1q_f32(y + 7 * d);
    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    float32x4_t neon_res5 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res6 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res7 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res8 = vmulq_f32(neon_base4, neon_base4);

    neon_base1 = vld1q_f32(y + 8 * d);
    neon_base2 = vld1q_f32(y + 9 * d);
    neon_base3 = vld1q_f32(y + 10 * d);
    neon_base4 = vld1q_f32(y + 11 * d);
    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    float32x4_t neon_res9 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res10 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res11 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res12 = vmulq_f32(neon_base4, neon_base4);

    neon_base1 = vld1q_f32(y + 12 * d);
    neon_base2 = vld1q_f32(y + 13 * d);
    neon_base3 = vld1q_f32(y + 14 * d);
    neon_base4 = vld1q_f32(y + 15 * d);
    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    float32x4_t neon_res13 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res14 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res15 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res16 = vmulq_f32(neon_base4, neon_base4);

    neon_base1 = vld1q_f32(y + 16 * d);
    neon_base2 = vld1q_f32(y + 17 * d);
    neon_base3 = vld1q_f32(y + 18 * d);
    neon_base4 = vld1q_f32(y + 19 * d);
    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    float32x4_t neon_res17 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res18 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res19 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res20 = vmulq_f32(neon_base4, neon_base4);

    neon_base1 = vld1q_f32(y + 20 * d);
    neon_base2 = vld1q_f32(y + 21 * d);
    neon_base3 = vld1q_f32(y + 22 * d);
    neon_base4 = vld1q_f32(y + 23 * d);
    neon_base1 = vsubq_f32(neon_base1, neon_query);
    neon_base2 = vsubq_f32(neon_base2, neon_query);
    neon_base3 = vsubq_f32(neon_base3, neon_query);
    neon_base4 = vsubq_f32(neon_base4, neon_query);
    float32x4_t neon_res21 = vmulq_f32(neon_base1, neon_base1);
    float32x4_t neon_res22 = vmulq_f32(neon_base2, neon_base2);
    float32x4_t neon_res23 = vmulq_f32(neon_base3, neon_base3);
    float32x4_t neon_res24 = vmulq_f32(neon_base4, neon_base4);
    for (i = single_round; i <= d - single_round; i += single_round) {
      neon_query = vld1q_f32(x + i);
      neon_base1 = vld1q_f32(y + i);
      neon_base2 = vld1q_f32(y + d + i);
      neon_base3 = vld1q_f32(y + 2 * d + i);
      neon_base4 = vld1q_f32(y + 3 * d + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_base1);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_base2);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_base3);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y + 4 * d + i);
      neon_base2 = vld1q_f32(y + 5 * d + i);
      neon_base3 = vld1q_f32(y + 6 * d + i);
      neon_base4 = vld1q_f32(y + 7 * d + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res5 = vmlaq_f32(neon_res5, neon_base1, neon_base1);
      neon_res6 = vmlaq_f32(neon_res6, neon_base2, neon_base2);
      neon_res7 = vmlaq_f32(neon_res7, neon_base3, neon_base3);
      neon_res8 = vmlaq_f32(neon_res8, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y + 8 * d + i);
      neon_base2 = vld1q_f32(y + 9 * d + i);
      neon_base3 = vld1q_f32(y + 10 * d + i);
      neon_base4 = vld1q_f32(y + 11 * d + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res9 = vmlaq_f32(neon_res9, neon_base1, neon_base1);
      neon_res10 = vmlaq_f32(neon_res10, neon_base2, neon_base2);
      neon_res11 = vmlaq_f32(neon_res11, neon_base3, neon_base3);
      neon_res12 = vmlaq_f32(neon_res12, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y + 12 * d + i);
      neon_base2 = vld1q_f32(y + 13 * d + i);
      neon_base3 = vld1q_f32(y + 14 * d + i);
      neon_base4 = vld1q_f32(y + 15 * d + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res13 = vmlaq_f32(neon_res13, neon_base1, neon_base1);
      neon_res14 = vmlaq_f32(neon_res14, neon_base2, neon_base2);
      neon_res15 = vmlaq_f32(neon_res15, neon_base3, neon_base3);
      neon_res16 = vmlaq_f32(neon_res16, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y + 16 * d + i);
      neon_base2 = vld1q_f32(y + 17 * d + i);
      neon_base3 = vld1q_f32(y + 18 * d + i);
      neon_base4 = vld1q_f32(y + 19 * d + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res17 = vmlaq_f32(neon_res17, neon_base1, neon_base1);
      neon_res18 = vmlaq_f32(neon_res18, neon_base2, neon_base2);
      neon_res19 = vmlaq_f32(neon_res19, neon_base3, neon_base3);
      neon_res20 = vmlaq_f32(neon_res20, neon_base4, neon_base4);

      neon_base1 = vld1q_f32(y + 20 * d + i);
      neon_base2 = vld1q_f32(y + 21 * d + i);
      neon_base3 = vld1q_f32(y + 22 * d + i);
      neon_base4 = vld1q_f32(y + 23 * d + i);
      neon_base1 = vsubq_f32(neon_base1, neon_query);
      neon_base2 = vsubq_f32(neon_base2, neon_query);
      neon_base3 = vsubq_f32(neon_base3, neon_query);
      neon_base4 = vsubq_f32(neon_base4, neon_query);
      neon_res21 = vmlaq_f32(neon_res21, neon_base1, neon_base1);
      neon_res22 = vmlaq_f32(neon_res22, neon_base2, neon_base2);
      neon_res23 = vmlaq_f32(neon_res23, neon_base3, neon_base3);
      neon_res24 = vmlaq_f32(neon_res24, neon_base4, neon_base4);
    }
    dis[0] = vaddvq_f32(neon_res1);
    dis[1] = vaddvq_f32(neon_res2);
    dis[2] = vaddvq_f32(neon_res3);
    dis[3] = vaddvq_f32(neon_res4);
    dis[4] = vaddvq_f32(neon_res5);
    dis[5] = vaddvq_f32(neon_res6);
    dis[6] = vaddvq_f32(neon_res7);
    dis[7] = vaddvq_f32(neon_res8);
    dis[8] = vaddvq_f32(neon_res9);
    dis[9] = vaddvq_f32(neon_res10);
    dis[10] = vaddvq_f32(neon_res11);
    dis[11] = vaddvq_f32(neon_res12);
    dis[12] = vaddvq_f32(neon_res13);
    dis[13] = vaddvq_f32(neon_res14);
    dis[14] = vaddvq_f32(neon_res15);
    dis[15] = vaddvq_f32(neon_res16);
    dis[16] = vaddvq_f32(neon_res17);
    dis[17] = vaddvq_f32(neon_res18);
    dis[18] = vaddvq_f32(neon_res19);
    dis[19] = vaddvq_f32(neon_res20);
    dis[20] = vaddvq_f32(neon_res21);
    dis[21] = vaddvq_f32(neon_res22);
    dis[22] = vaddvq_f32(neon_res23);
    dis[23] = vaddvq_f32(neon_res24);
  } else {
    for (int i = 0; i < 24; i++) {
      dis[i] = 0.0f;
    }
    i = 0;
  }
  if (i < d) {
    float q0 = x[i] - *(y + i);
    float q1 = x[i] - *(y + d + i);
    float q2 = x[i] - *(y + 2 * d + i);
    float q3 = x[i] - *(y + 3 * d + i);
    float q4 = x[i] - *(y + 4 * d + i);
    float q5 = x[i] - *(y + 5 * d + i);
    float q6 = x[i] - *(y + 6 * d + i);
    float q7 = x[i] - *(y + 7 * d + i);
    float d0 = q0 * q0;
    float d1 = q1 * q1;
    float d2 = q2 * q2;
    float d3 = q3 * q3;
    float d4 = q4 * q4;
    float d5 = q5 * q5;
    float d6 = q6 * q6;
    float d7 = q7 * q7;
    q0 = x[i] - *(y + 8 * d + i);
    q1 = x[i] - *(y + 9 * d + i);
    q2 = x[i] - *(y + 10 * d + i);
    q3 = x[i] - *(y + 11 * d + i);
    q4 = x[i] - *(y + 12 * d + i);
    q5 = x[i] - *(y + 13 * d + i);
    q6 = x[i] - *(y + 14 * d + i);
    q7 = x[i] - *(y + 15 * d + i);
    float d8 = q0 * q0;
    float d9 = q1 * q1;
    float d10 = q2 * q2;
    float d11 = q3 * q3;
    float d12 = q4 * q4;
    float d13 = q5 * q5;
    float d14 = q6 * q6;
    float d15 = q7 * q7;
    q0 = x[i] - *(y + 16 * d + i);
    q1 = x[i] - *(y + 17 * d + i);
    q2 = x[i] - *(y + 18 * d + i);
    q3 = x[i] - *(y + 19 * d + i);
    q4 = x[i] - *(y + 20 * d + i);
    q5 = x[i] - *(y + 21 * d + i);
    q6 = x[i] - *(y + 22 * d + i);
    q7 = x[i] - *(y + 23 * d + i);
    float d16 = q0 * q0;
    float d17 = q1 * q1;
    float d18 = q2 * q2;
    float d19 = q3 * q3;
    float d20 = q4 * q4;
    float d21 = q5 * q5;
    float d22 = q6 * q6;
    float d23 = q7 * q7;
    for (i++; i < d; ++i) {
      q0 = x[i] - *(y + i);
      q1 = x[i] - *(y + d + i);
      q2 = x[i] - *(y + 2 * d + i);
      q3 = x[i] - *(y + 3 * d + i);
      q4 = x[i] - *(y + 4 * d + i);
      q5 = x[i] - *(y + 5 * d + i);
      q6 = x[i] - *(y + 6 * d + i);
      q7 = x[i] - *(y + 7 * d + i);
      d0 += q0 * q0;
      d1 += q1 * q1;
      d2 += q2 * q2;
      d3 += q3 * q3;
      d4 += q4 * q4;
      d5 += q5 * q5;
      d6 += q6 * q6;
      d7 += q7 * q7;
      q0 = x[i] - *(y + 8 * d + i);
      q1 = x[i] - *(y + 9 * d + i);
      q2 = x[i] - *(y + 10 * d + i);
      q3 = x[i] - *(y + 11 * d + i);
      q4 = x[i] - *(y + 12 * d + i);
      q5 = x[i] - *(y + 13 * d + i);
      q6 = x[i] - *(y + 14 * d + i);
      q7 = x[i] - *(y + 15 * d + i);
      d8 += q0 * q0;
      d9 += q1 * q1;
      d10 += q2 * q2;
      d11 += q3 * q3;
      d12 += q4 * q4;
      d13 += q5 * q5;
      d14 += q6 * q6;
      d15 += q7 * q7;
      q0 = x[i] - *(y + 16 * d + i);
      q1 = x[i] - *(y + 17 * d + i);
      q2 = x[i] - *(y + 18 * d + i);
      q3 = x[i] - *(y + 19 * d + i);
      q4 = x[i] - *(y + 20 * d + i);
      q5 = x[i] - *(y + 21 * d + i);
      q6 = x[i] - *(y + 22 * d + i);
      q7 = x[i] - *(y + 23 * d + i);
      d16 += q0 * q0;
      d17 += q1 * q1;
      d18 += q2 * q2;
      d19 += q3 * q3;
      d20 += q4 * q4;
      d21 += q5 * q5;
      d22 += q6 * q6;
      d23 += q7 * q7;
    }
    dis[0] += d0;
    dis[1] += d1;
    dis[2] += d2;
    dis[3] += d3;
    dis[4] += d4;
    dis[5] += d5;
    dis[6] += d6;
    dis[7] += d7;
    dis[8] += d8;
    dis[9] += d9;
    dis[10] += d10;
    dis[11] += d11;
    dis[12] += d12;
    dis[13] += d13;
    dis[14] += d14;
    dis[15] += d15;
    dis[16] += d16;
    dis[17] += d17;
    dis[18] += d18;
    dis[19] += d19;
    dis[20] += d20;
    dis[21] += d21;
    dis[22] += d22;
    dis[23] += d23;
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute L2 squares for 16 vectors with float precision and store results in dis array.
* @param dis Pointer to the output array for storing the results (float).
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_L2sqr_continuous_transpose_large_kernel(float *dis, const float *x, const float *y, const size_t d)
{
  float32x4_t neon_res[16];
  float32x4_t neon_base[8];
  float32x4_t single_query = vdupq_n_f32(x[0]);
  prefetch_Lx(y + 64);
  neon_base[0] = vld1q_f32(y);
  neon_base[1] = vld1q_f32(y + 4);
  neon_base[2] = vld1q_f32(y + 8);
  neon_base[3] = vld1q_f32(y + 12);
  neon_base[4] = vld1q_f32(y + 16);
  neon_base[5] = vld1q_f32(y + 20);
  neon_base[6] = vld1q_f32(y + 24);
  neon_base[7] = vld1q_f32(y + 28);

  neon_base[0] = vsubq_f32(neon_base[0], single_query);
  neon_base[1] = vsubq_f32(neon_base[1], single_query);
  neon_base[2] = vsubq_f32(neon_base[2], single_query);
  neon_base[3] = vsubq_f32(neon_base[3], single_query);
  neon_base[4] = vsubq_f32(neon_base[4], single_query);
  neon_base[5] = vsubq_f32(neon_base[5], single_query);
  neon_base[6] = vsubq_f32(neon_base[6], single_query);
  neon_base[7] = vsubq_f32(neon_base[7], single_query);

  neon_res[0] = vmulq_f32(neon_base[0], neon_base[0]);
  neon_res[1] = vmulq_f32(neon_base[1], neon_base[1]);
  neon_res[2] = vmulq_f32(neon_base[2], neon_base[2]);
  neon_res[3] = vmulq_f32(neon_base[3], neon_base[3]);
  neon_res[4] = vmulq_f32(neon_base[4], neon_base[4]);
  neon_res[5] = vmulq_f32(neon_base[5], neon_base[5]);
  neon_res[6] = vmulq_f32(neon_base[6], neon_base[6]);
  neon_res[7] = vmulq_f32(neon_base[7], neon_base[7]);

  neon_base[0] = vld1q_f32(y + 32);
  neon_base[1] = vld1q_f32(y + 36);
  neon_base[2] = vld1q_f32(y + 40);
  neon_base[3] = vld1q_f32(y + 44);
  neon_base[4] = vld1q_f32(y + 48);
  neon_base[5] = vld1q_f32(y + 52);
  neon_base[6] = vld1q_f32(y + 56);
  neon_base[7] = vld1q_f32(y + 60);

  neon_base[0] = vsubq_f32(neon_base[0], single_query);
  neon_base[1] = vsubq_f32(neon_base[1], single_query);
  neon_base[2] = vsubq_f32(neon_base[2], single_query);
  neon_base[3] = vsubq_f32(neon_base[3], single_query);
  neon_base[4] = vsubq_f32(neon_base[4], single_query);
  neon_base[5] = vsubq_f32(neon_base[5], single_query);
  neon_base[6] = vsubq_f32(neon_base[6], single_query);
  neon_base[7] = vsubq_f32(neon_base[7], single_query);

  neon_res[8] = vmulq_f32(neon_base[0], neon_base[0]);
  neon_res[9] = vmulq_f32(neon_base[1], neon_base[1]);
  neon_res[10] = vmulq_f32(neon_base[2], neon_base[2]);
  neon_res[11] = vmulq_f32(neon_base[3], neon_base[3]);
  neon_res[12] = vmulq_f32(neon_base[4], neon_base[4]);
  neon_res[13] = vmulq_f32(neon_base[5], neon_base[5]);
  neon_res[14] = vmulq_f32(neon_base[6], neon_base[6]);
  neon_res[15] = vmulq_f32(neon_base[7], neon_base[7]);

  /* dim loop */
  for (size_t i = 1; i < d; ++i) {
    single_query = vdupq_n_f32(x[i]);
    prefetch_Lx(y + 64 * (i + 1));

    neon_base[0] = vld1q_f32(y + 64 * i);
    neon_base[1] = vld1q_f32(y + 64 * i + 4);
    neon_base[2] = vld1q_f32(y + 64 * i + 8);
    neon_base[3] = vld1q_f32(y + 64 * i + 12);
    neon_base[4] = vld1q_f32(y + 64 * i + 16);
    neon_base[5] = vld1q_f32(y + 64 * i + 20);
    neon_base[6] = vld1q_f32(y + 64 * i + 24);
    neon_base[7] = vld1q_f32(y + 64 * i + 28);

    neon_base[0] = vsubq_f32(neon_base[0], single_query);
    neon_base[1] = vsubq_f32(neon_base[1], single_query);
    neon_base[2] = vsubq_f32(neon_base[2], single_query);
    neon_base[3] = vsubq_f32(neon_base[3], single_query);
    neon_base[4] = vsubq_f32(neon_base[4], single_query);
    neon_base[5] = vsubq_f32(neon_base[5], single_query);
    neon_base[6] = vsubq_f32(neon_base[6], single_query);
    neon_base[7] = vsubq_f32(neon_base[7], single_query);

    neon_res[0] = vmlaq_f32(neon_res[0], neon_base[0], neon_base[0]);
    neon_res[1] = vmlaq_f32(neon_res[1], neon_base[1], neon_base[1]);
    neon_res[2] = vmlaq_f32(neon_res[2], neon_base[2], neon_base[2]);
    neon_res[3] = vmlaq_f32(neon_res[3], neon_base[3], neon_base[3]);
    neon_res[4] = vmlaq_f32(neon_res[4], neon_base[4], neon_base[4]);
    neon_res[5] = vmlaq_f32(neon_res[5], neon_base[5], neon_base[5]);
    neon_res[6] = vmlaq_f32(neon_res[6], neon_base[6], neon_base[6]);
    neon_res[7] = vmlaq_f32(neon_res[7], neon_base[7], neon_base[7]);

    neon_base[0] = vld1q_f32(y + 64 * i + 32);
    neon_base[1] = vld1q_f32(y + 64 * i + 36);
    neon_base[2] = vld1q_f32(y + 64 * i + 40);
    neon_base[3] = vld1q_f32(y + 64 * i + 44);
    neon_base[4] = vld1q_f32(y + 64 * i + 48);
    neon_base[5] = vld1q_f32(y + 64 * i + 52);
    neon_base[6] = vld1q_f32(y + 64 * i + 56);
    neon_base[7] = vld1q_f32(y + 64 * i + 60);

    neon_base[0] = vsubq_f32(neon_base[0], single_query);
    neon_base[1] = vsubq_f32(neon_base[1], single_query);
    neon_base[2] = vsubq_f32(neon_base[2], single_query);
    neon_base[3] = vsubq_f32(neon_base[3], single_query);
    neon_base[4] = vsubq_f32(neon_base[4], single_query);
    neon_base[5] = vsubq_f32(neon_base[5], single_query);
    neon_base[6] = vsubq_f32(neon_base[6], single_query);
    neon_base[7] = vsubq_f32(neon_base[7], single_query);

    neon_res[8] = vmlaq_f32(neon_res[8], neon_base[0], neon_base[0]);
    neon_res[9] = vmlaq_f32(neon_res[9], neon_base[1], neon_base[1]);
    neon_res[10] = vmlaq_f32(neon_res[10], neon_base[2], neon_base[2]);
    neon_res[11] = vmlaq_f32(neon_res[11], neon_base[3], neon_base[3]);
    neon_res[12] = vmlaq_f32(neon_res[12], neon_base[4], neon_base[4]);
    neon_res[13] = vmlaq_f32(neon_res[13], neon_base[5], neon_base[5]);
    neon_res[14] = vmlaq_f32(neon_res[14], neon_base[6], neon_base[6]);
    neon_res[15] = vmlaq_f32(neon_res[15], neon_base[7], neon_base[7]);
  }
  {
    vst1q_f32(dis, neon_res[0]);
    vst1q_f32(dis + 4, neon_res[1]);
    vst1q_f32(dis + 8, neon_res[2]);
    vst1q_f32(dis + 12, neon_res[3]);
    vst1q_f32(dis + 16, neon_res[4]);
    vst1q_f32(dis + 20, neon_res[5]);
    vst1q_f32(dis + 24, neon_res[6]);
    vst1q_f32(dis + 28, neon_res[7]);
    vst1q_f32(dis + 32, neon_res[8]);
    vst1q_f32(dis + 36, neon_res[9]);
    vst1q_f32(dis + 40, neon_res[10]);
    vst1q_f32(dis + 44, neon_res[11]);
    vst1q_f32(dis + 48, neon_res[12]);
    vst1q_f32(dis + 52, neon_res[13]);
    vst1q_f32(dis + 56, neon_res[14]);
    vst1q_f32(dis + 60, neon_res[15]);
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute L2 squares for 8 vectors with float precision and store results in dis array.
* @param dis Pointer to the output array for storing the results (float).
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_L2sqr_continuous_transpose_medium_kernel(float *dis, const float *x, const float *y, const size_t d)
{
  float32x4_t neon_res[8];
  float32x4_t neon_base[8];
  float32x4_t neon_diff[8];
  float32x4_t single_query = vdupq_n_f32(x[0]);
  neon_base[0] = vld1q_f32(y);
  neon_base[1] = vld1q_f32(y + 4);
  neon_base[2] = vld1q_f32(y + 8);
  neon_base[3] = vld1q_f32(y + 12);
  neon_base[4] = vld1q_f32(y + 16);
  neon_base[5] = vld1q_f32(y + 20);
  neon_base[6] = vld1q_f32(y + 24);
  neon_base[7] = vld1q_f32(y + 28);

  neon_diff[0] = vsubq_f32(neon_base[0], single_query);
  neon_diff[1] = vsubq_f32(neon_base[1], single_query);
  neon_diff[2] = vsubq_f32(neon_base[2], single_query);
  neon_diff[3] = vsubq_f32(neon_base[3], single_query);
  neon_diff[4] = vsubq_f32(neon_base[4], single_query);
  neon_diff[5] = vsubq_f32(neon_base[5], single_query);
  neon_diff[6] = vsubq_f32(neon_base[6], single_query);
  neon_diff[7] = vsubq_f32(neon_base[7], single_query);

  if (unlikely(d == 1)) {
    neon_res[0] = vmulq_f32(neon_diff[0], neon_diff[0]);
    neon_res[1] = vmulq_f32(neon_diff[1], neon_diff[1]);
    neon_res[2] = vmulq_f32(neon_diff[2], neon_diff[2]);
    neon_res[3] = vmulq_f32(neon_diff[3], neon_diff[3]);
    neon_res[4] = vmulq_f32(neon_diff[4], neon_diff[4]);
    neon_res[5] = vmulq_f32(neon_diff[5], neon_diff[5]);
    neon_res[6] = vmulq_f32(neon_diff[6], neon_diff[6]);
    neon_res[7] = vmulq_f32(neon_diff[7], neon_diff[7]);
  } else {
    single_query = vdupq_n_f32(x[1]);
    neon_base[0] = vld1q_f32(y + 32);
    neon_base[1] = vld1q_f32(y + 36);
    neon_base[2] = vld1q_f32(y + 40);
    neon_base[3] = vld1q_f32(y + 44);
    neon_base[4] = vld1q_f32(y + 48);
    neon_base[5] = vld1q_f32(y + 52);
    neon_base[6] = vld1q_f32(y + 56);
    neon_base[7] = vld1q_f32(y + 60);

    neon_res[0] = vmulq_f32(neon_diff[0], neon_diff[0]);
    neon_res[1] = vmulq_f32(neon_diff[1], neon_diff[1]);
    neon_res[2] = vmulq_f32(neon_diff[2], neon_diff[2]);
    neon_res[3] = vmulq_f32(neon_diff[3], neon_diff[3]);
    neon_res[4] = vmulq_f32(neon_diff[4], neon_diff[4]);
    neon_res[5] = vmulq_f32(neon_diff[5], neon_diff[5]);
    neon_res[6] = vmulq_f32(neon_diff[6], neon_diff[6]);
    neon_res[7] = vmulq_f32(neon_diff[7], neon_diff[7]);
    /* dim loop */
    for (size_t i = 2; i < d; ++i) {
      neon_diff[0] = vsubq_f32(neon_base[0], single_query);
      neon_diff[1] = vsubq_f32(neon_base[1], single_query);
      neon_diff[2] = vsubq_f32(neon_base[2], single_query);
      neon_diff[3] = vsubq_f32(neon_base[3], single_query);
      neon_diff[4] = vsubq_f32(neon_base[4], single_query);
      neon_diff[5] = vsubq_f32(neon_base[5], single_query);
      neon_diff[6] = vsubq_f32(neon_base[6], single_query);
      neon_diff[7] = vsubq_f32(neon_base[7], single_query);

      single_query = vdupq_n_f32(x[i]);
      neon_base[0] = vld1q_f32(y + 32 * i);
      neon_base[1] = vld1q_f32(y + 32 * i + 4);
      neon_base[2] = vld1q_f32(y + 32 * i + 8);
      neon_base[3] = vld1q_f32(y + 32 * i + 12);
      neon_base[4] = vld1q_f32(y + 32 * i + 16);
      neon_base[5] = vld1q_f32(y + 32 * i + 20);
      neon_base[6] = vld1q_f32(y + 32 * i + 24);
      neon_base[7] = vld1q_f32(y + 32 * i + 28);

      neon_res[0] = vmlaq_f32(neon_res[0], neon_diff[0], neon_diff[0]);
      neon_res[1] = vmlaq_f32(neon_res[1], neon_diff[1], neon_diff[1]);
      neon_res[2] = vmlaq_f32(neon_res[2], neon_diff[2], neon_diff[2]);
      neon_res[3] = vmlaq_f32(neon_res[3], neon_diff[3], neon_diff[3]);
      neon_res[4] = vmlaq_f32(neon_res[4], neon_diff[4], neon_diff[4]);
      neon_res[5] = vmlaq_f32(neon_res[5], neon_diff[5], neon_diff[5]);
      neon_res[6] = vmlaq_f32(neon_res[6], neon_diff[6], neon_diff[6]);
      neon_res[7] = vmlaq_f32(neon_res[7], neon_diff[7], neon_diff[7]);
    }
    {
      neon_diff[0] = vsubq_f32(neon_base[0], single_query);
      neon_diff[1] = vsubq_f32(neon_base[1], single_query);
      neon_diff[2] = vsubq_f32(neon_base[2], single_query);
      neon_diff[3] = vsubq_f32(neon_base[3], single_query);
      neon_diff[4] = vsubq_f32(neon_base[4], single_query);
      neon_diff[5] = vsubq_f32(neon_base[5], single_query);
      neon_diff[6] = vsubq_f32(neon_base[6], single_query);
      neon_diff[7] = vsubq_f32(neon_base[7], single_query);

      neon_res[0] = vmlaq_f32(neon_res[0], neon_diff[0], neon_diff[0]);
      neon_res[1] = vmlaq_f32(neon_res[1], neon_diff[1], neon_diff[1]);
      neon_res[2] = vmlaq_f32(neon_res[2], neon_diff[2], neon_diff[2]);
      neon_res[3] = vmlaq_f32(neon_res[3], neon_diff[3], neon_diff[3]);
      neon_res[4] = vmlaq_f32(neon_res[4], neon_diff[4], neon_diff[4]);
      neon_res[5] = vmlaq_f32(neon_res[5], neon_diff[5], neon_diff[5]);
      neon_res[6] = vmlaq_f32(neon_res[6], neon_diff[6], neon_diff[6]);
      neon_res[7] = vmlaq_f32(neon_res[7], neon_diff[7], neon_diff[7]);
    }
  }
  vst1q_f32(dis, neon_res[0]);
  vst1q_f32(dis + 4, neon_res[1]);
  vst1q_f32(dis + 8, neon_res[2]);
  vst1q_f32(dis + 12, neon_res[3]);
  vst1q_f32(dis + 16, neon_res[4]);
  vst1q_f32(dis + 20, neon_res[5]);
  vst1q_f32(dis + 24, neon_res[6]);
  vst1q_f32(dis + 28, neon_res[7]);
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute L2 squares for 4 vectors with float precision and store results in dis array.
* @param dis Pointer to the output array for storing the results (float).
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_L2sqr_continuous_transpose_mini_kernel(float *dis, const float *x, const float *y, const size_t d)
{
  float32x4_t neon_res[4];
  float32x4_t single_query = vdupq_n_f32(x[0]);
  float32x4_t neon_base1 = vld1q_f32(y);
  float32x4_t neon_base2 = vld1q_f32(y + 4);
  float32x4_t neon_base3 = vld1q_f32(y + 8);
  float32x4_t neon_base4 = vld1q_f32(y + 12);
  float32x4_t neon_diff1 = vsubq_f32(neon_base1, single_query);
  float32x4_t neon_diff2 = vsubq_f32(neon_base2, single_query);
  float32x4_t neon_diff3 = vsubq_f32(neon_base3, single_query);
  float32x4_t neon_diff4 = vsubq_f32(neon_base4, single_query);
  if (unlikely(d == 1)) {
    neon_res[0] = vmulq_f32(neon_diff1, neon_diff1);
    neon_res[1] = vmulq_f32(neon_diff2, neon_diff2);
    neon_res[2] = vmulq_f32(neon_diff3, neon_diff3);
    neon_res[3] = vmulq_f32(neon_diff4, neon_diff4);
  } else {
    single_query = vdupq_n_f32(x[1]);
    neon_base1 = vld1q_f32(y + 16);
    neon_base2 = vld1q_f32(y + 20);
    neon_base3 = vld1q_f32(y + 24);
    neon_base4 = vld1q_f32(y + 28);
    neon_res[0] = vmulq_f32(neon_diff1, neon_diff1);
    neon_res[1] = vmulq_f32(neon_diff2, neon_diff2);
    neon_res[2] = vmulq_f32(neon_diff3, neon_diff3);
    neon_res[3] = vmulq_f32(neon_diff4, neon_diff4);
    for (size_t i = 2; i < d; ++i) {
      neon_diff1 = vsubq_f32(neon_base1, single_query);
      neon_diff2 = vsubq_f32(neon_base2, single_query);
      neon_diff3 = vsubq_f32(neon_base3, single_query);
      neon_diff4 = vsubq_f32(neon_base4, single_query);

      single_query = vdupq_n_f32(x[i]);
      neon_base1 = vld1q_f32(y + 16 * i);
      neon_base2 = vld1q_f32(y + 16 * i + 4);
      neon_base3 = vld1q_f32(y + 16 * i + 8);
      neon_base4 = vld1q_f32(y + 16 * i + 12);

      neon_res[0] = vmlaq_f32(neon_res[0], neon_diff1, neon_diff1);
      neon_res[1] = vmlaq_f32(neon_res[1], neon_diff2, neon_diff2);
      neon_res[2] = vmlaq_f32(neon_res[2], neon_diff3, neon_diff3);
      neon_res[3] = vmlaq_f32(neon_res[3], neon_diff4, neon_diff4);
    }
    {
      neon_diff1 = vsubq_f32(neon_base1, single_query);
      neon_diff2 = vsubq_f32(neon_base2, single_query);
      neon_diff3 = vsubq_f32(neon_base3, single_query);
      neon_diff4 = vsubq_f32(neon_base4, single_query);

      neon_res[0] = vmlaq_f32(neon_res[0], neon_diff1, neon_diff1);
      neon_res[1] = vmlaq_f32(neon_res[1], neon_diff2, neon_diff2);
      neon_res[2] = vmlaq_f32(neon_res[2], neon_diff3, neon_diff3);
      neon_res[3] = vmlaq_f32(neon_res[3], neon_diff4, neon_diff4);
    }
  }

  vst1q_f32(dis, neon_res[0]);
  vst1q_f32(dis + 4, neon_res[1]);
  vst1q_f32(dis + 8, neon_res[2]);
  vst1q_f32(dis + 12, neon_res[3]);
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute L2 squares for a batch of vectors based on given indices.
* @param dis Pointer to the output array for storing the results (float).
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param ids Pointer to the indices array for selecting database vectors.
* @param d Dimension of the vectors.
* @param ny Number of database vectors to process.
* @param dis_size Length of dis.
*/
int krl_L2sqr_by_idx(
  float *dis, const float *x, const float *y, const int64_t *ids, size_t d, size_t ny, size_t dis_size)
{
  size_t i = 0;
  const float *__restrict listy[24];

  if (d < 1 || d > 65535 || ny < 1 || ny > 1ULL << 30) {
    std::printf("Error: INVALPARAM in krl_L2sqr_by_idx\n");
    return INVALPARAM;
  }

  if (x == nullptr || y == nullptr || ids == nullptr || dis == nullptr || dis_size < ny) {
    std::printf("Error: INVALPOINTER in krl_L2sqr_by_idx\n");
    return INVALPOINTER;
  }

  for (; i + 24 <= ny; i += 24) {
    prefetch_L1(x);
    listy[0] = (const float *)(y + *(ids + i) * d);
    prefetch_Lx(listy[0]);
    listy[1] = (const float *)(y + *(ids + i + 1) * d);
    prefetch_Lx(listy[1]);
    listy[2] = (const float *)(y + *(ids + i + 2) * d);
    prefetch_Lx(listy[2]);
    listy[3] = (const float *)(y + *(ids + i + 3) * d);
    prefetch_Lx(listy[3]);
    listy[4] = (const float *)(y + *(ids + i + 4) * d);
    prefetch_Lx(listy[4]);
    listy[5] = (const float *)(y + *(ids + i + 5) * d);
    prefetch_Lx(listy[5]);
    listy[6] = (const float *)(y + *(ids + i + 6) * d);
    prefetch_Lx(listy[6]);
    listy[7] = (const float *)(y + *(ids + i + 7) * d);
    prefetch_Lx(listy[7]);
    listy[8] = (const float *)(y + *(ids + i + 8) * d);
    prefetch_Lx(listy[8]);
    listy[9] = (const float *)(y + *(ids + i + 9) * d);
    prefetch_Lx(listy[9]);
    listy[10] = (const float *)(y + *(ids + i + 10) * d);
    prefetch_Lx(listy[10]);
    listy[11] = (const float *)(y + *(ids + i + 11) * d);
    prefetch_Lx(listy[11]);
    listy[12] = (const float *)(y + *(ids + i + 12) * d);
    prefetch_Lx(listy[12]);
    listy[13] = (const float *)(y + *(ids + i + 13) * d);
    prefetch_Lx(listy[13]);
    listy[14] = (const float *)(y + *(ids + i + 14) * d);
    prefetch_Lx(listy[14]);
    listy[15] = (const float *)(y + *(ids + i + 15) * d);
    prefetch_Lx(listy[15]);
    listy[16] = (const float *)(y + *(ids + i + 16) * d);
    prefetch_Lx(listy[16]);
    listy[17] = (const float *)(y + *(ids + i + 17) * d);
    prefetch_Lx(listy[17]);
    listy[18] = (const float *)(y + *(ids + i + 18) * d);
    prefetch_Lx(listy[18]);
    listy[19] = (const float *)(y + *(ids + i + 19) * d);
    prefetch_Lx(listy[19]);
    listy[20] = (const float *)(y + *(ids + i + 20) * d);
    prefetch_Lx(listy[20]);
    listy[21] = (const float *)(y + *(ids + i + 21) * d);
    prefetch_Lx(listy[21]);
    listy[22] = (const float *)(y + *(ids + i + 22) * d);
    prefetch_Lx(listy[22]);
    listy[23] = (const float *)(y + *(ids + i + 23) * d);
    prefetch_Lx(listy[23]);
    krl_L2sqr_idx_prefetch_batch24(x, listy, d, dis + i);
  }
  if (i + 16 <= ny) {
    prefetch_L1(x);
    listy[0] = (const float *)(y + *(ids + i) * d);
    prefetch_Lx(listy[0]);
    listy[1] = (const float *)(y + *(ids + i + 1) * d);
    prefetch_Lx(listy[1]);
    listy[2] = (const float *)(y + *(ids + i + 2) * d);
    prefetch_Lx(listy[2]);
    listy[3] = (const float *)(y + *(ids + i + 3) * d);
    prefetch_Lx(listy[3]);
    listy[4] = (const float *)(y + *(ids + i + 4) * d);
    prefetch_Lx(listy[4]);
    listy[5] = (const float *)(y + *(ids + i + 5) * d);
    prefetch_Lx(listy[5]);
    listy[6] = (const float *)(y + *(ids + i + 6) * d);
    prefetch_Lx(listy[6]);
    listy[7] = (const float *)(y + *(ids + i + 7) * d);
    prefetch_Lx(listy[7]);
    listy[8] = (const float *)(y + *(ids + i + 8) * d);
    prefetch_Lx(listy[8]);
    listy[9] = (const float *)(y + *(ids + i + 9) * d);
    prefetch_Lx(listy[9]);
    listy[10] = (const float *)(y + *(ids + i + 10) * d);
    prefetch_Lx(listy[10]);
    listy[11] = (const float *)(y + *(ids + i + 11) * d);
    prefetch_Lx(listy[11]);
    listy[12] = (const float *)(y + *(ids + i + 12) * d);
    prefetch_Lx(listy[12]);
    listy[13] = (const float *)(y + *(ids + i + 13) * d);
    prefetch_Lx(listy[13]);
    listy[14] = (const float *)(y + *(ids + i + 14) * d);
    prefetch_Lx(listy[14]);
    listy[15] = (const float *)(y + *(ids + i + 15) * d);
    prefetch_Lx(listy[15]);
    krl_L2sqr_idx_prefetch_batch16(x, listy, d, dis + i);
    i += 16;
  } else if (i + 8 <= ny) {
    prefetch_L1(x);
    listy[0] = (const float *)(y + *(ids + i) * d);
    prefetch_Lx(listy[0]);
    listy[1] = (const float *)(y + *(ids + i + 1) * d);
    prefetch_Lx(listy[1]);
    listy[2] = (const float *)(y + *(ids + i + 2) * d);
    prefetch_Lx(listy[2]);
    listy[3] = (const float *)(y + *(ids + i + 3) * d);
    prefetch_Lx(listy[3]);
    listy[4] = (const float *)(y + *(ids + i + 4) * d);
    prefetch_Lx(listy[4]);
    listy[5] = (const float *)(y + *(ids + i + 5) * d);
    prefetch_Lx(listy[5]);
    listy[6] = (const float *)(y + *(ids + i + 6) * d);
    prefetch_Lx(listy[6]);
    listy[7] = (const float *)(y + *(ids + i + 7) * d);
    prefetch_Lx(listy[7]);
    krl_L2sqr_idx_prefetch_batch8(x, listy, d, dis + i);
    i += 8;
  }
  if (ny & 4) {
    listy[0] = (const float *)(y + *(ids + i) * d);
    listy[1] = (const float *)(y + *(ids + i + 1) * d);
    listy[2] = (const float *)(y + *(ids + i + 2) * d);
    listy[3] = (const float *)(y + *(ids + i + 3) * d);
    krl_L2sqr_idx_batch4(x, listy, d, dis + i);
    i += 4;
  }
  if (ny & 2) {
    const float *y0 = y + *(ids + i) * d;
    const float *y1 = y + *(ids + i + 1) * d;
    krl_L2sqr_idx_batch2(x, y0, y1, d, dis + i);
    i += 2;
  }
  if (ny & 1) {
    krl_L2sqr(x, y + d * ids[i], d, &dis[i], 1);
  }
  return SUCCESS;
}

/*
* @brief Compute L2 squares for a batch of vectors.
* @param dis Pointer to the output array for storing the results (float).
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param ny Number of database vectors to process.
* @param d Dimension of the vectors.
* @param dis_size Length of dis.
*/
int krl_L2sqr_ny(float *dis, const float *x, const float *y, const size_t ny, const size_t d, size_t dis_size)
{
  size_t i = 0;

  if (d < 1 || d > 65535 || ny < 1 || ny > 1ULL << 30) {
    std::printf("Error: INVALPARAM in krl_L2sqr_ny\n");
    return INVALPARAM;
  }

  if (x == nullptr || y == nullptr || dis == nullptr || dis_size < ny) {
    std::printf("Error: INVALPOINTER in krl_L2sqr_ny\n");
    return INVALPOINTER;
  }

  for (; i + 24 <= ny; i += 24) {
    krl_L2sqr_batch24(x, y + i * d, d, dis + i);
  }
  if (i + 16 <= ny) {
    krl_L2sqr_batch16(x, y + i * d, d, dis + i);
    i += 16;
  } else if (i + 8 <= ny) {
    krl_L2sqr_batch8(x, y + i * d, d, dis + i);
    i += 8;
  }
  if (ny & 4) {
    krl_L2sqr_batch4(x, y + i * d, d, dis + i);
    i += 4;
  }
  if (ny & 2) {
    krl_L2sqr_batch2(x, y + i * d, d, dis + i);
  }
  if (ny & 1) {
    const float *y0 = (y + (ny - 1) * d);
    krl_L2sqr(x, y0, d, &dis[ny - 1], 1);
  }
  return SUCCESS;
}

/*
* @brief Compute L2 squares for a batch of vectors with a given handle.
* @param kdh Pointer to the distance handle containing configuration and data.
* @param dis Pointer to the output array for storing the results (float).
* @param x Pointer to the query vector (float).
* @param dis_size Length of dis.
* @param x_size Length of x.
*/
int krl_L2sqr_ny_with_handle(const KRLDistanceHandle *kdh, float *dis, const float *x, size_t dis_size, size_t x_size)
{
  if (kdh == nullptr || dis == nullptr || x == nullptr) {
    std::printf("Error: INVALPOINTER in krl_L2sqr_ny_with_handle\n");
    return INVALPOINTER;
  }
  const size_t ny = kdh->ny;
  const size_t dim = kdh->d;
  const size_t M = kdh->M;
  if (dis_size < M * ny || x_size < dim * M) {
    std::printf("Error: INVALPARAM in krl_L2sqr_ny_with_handle\n");
    return INVALPARAM;
  }

  if (kdh->data_bits == 32) {
    const float *y = (const float *)kdh->transposed_codes;
    const size_t ceil_ny = kdh->ceil_ny;
    const size_t left = ny & (kdh->blocksize - 1);
    switch (kdh->blocksize) {
      case 16:
        if (left) {
          float distance_tmp_buffer[16];
          for (size_t m = 0; m < M; m++) {
            size_t i = 0;
            for (; i + 16 <= ny; i += 16) {
              krl_L2sqr_continuous_transpose_mini_kernel(dis + i, x, y + i * dim, dim);
            }
            krl_L2sqr_continuous_transpose_mini_kernel(distance_tmp_buffer, x, y + i * dim, dim);
            size_t remaining_dis_size = dis_size - (m * ny + i);
            if (remaining_dis_size < left) {
              std::printf("Error: UNSAFEMEM in krl_L2sqr_ny_with_handle\n");
              return UNSAFEMEM;
            }
            int ret = SafeMemory::CheckAndMemcpy(
              dis + i, remaining_dis_size * sizeof(float), distance_tmp_buffer, left * sizeof(float));
            if (ret != 0) {
              std::printf("Error: UNSAFEMEM in krl_L2sqr_ny_with_handle\n");
              return UNSAFEMEM;
            }
            dis += ny;
            x += dim;
            y += ceil_ny * dim;
          }
        } else {
          for (size_t m = 0; m < M; m++) {
            for (size_t i = 0; i < ny; i += 16) {
              krl_L2sqr_continuous_transpose_mini_kernel(dis + i, x, y + i * dim, dim);
            }
            dis += ny;
            x += dim;
            y += ceil_ny * dim;
          }
        }
        break;
      case 32:
        if (left) {
          float distance_tmp_buffer[32];
          for (size_t m = 0; m < M; m++) {
            size_t i = 0;
            for (; i + 32 <= ny; i += 32) {
              krl_L2sqr_continuous_transpose_medium_kernel(dis + i, x, y + i * dim, dim);
            }
            krl_L2sqr_continuous_transpose_medium_kernel(distance_tmp_buffer, x, y + i * dim, dim);
            size_t remaining_dis_size = dis_size - (m * ny + i);
            if (remaining_dis_size < left) {
              std::printf("Error: UNSAFEMEM in krl_L2sqr_ny_with_handle\n");
              return UNSAFEMEM;
            }
            int ret = SafeMemory::CheckAndMemcpy(
              dis + i, remaining_dis_size * sizeof(float), distance_tmp_buffer, left * sizeof(float));
            if (ret != 0) {
              std::printf("Error: UNSAFEMEM in krl_L2sqr_ny_with_handle\n");
              return UNSAFEMEM;
            }
            dis += ny;
            x += dim;
            y += ceil_ny * dim;
          }
        } else {
          for (size_t m = 0; m < M; m++) {
            for (size_t i = 0; i < ny; i += 32) {
              krl_L2sqr_continuous_transpose_medium_kernel(dis + i, x, y + i * dim, dim);
            }
            dis += ny;
            x += dim;
            y += ceil_ny * dim;
          }
        }
        break;
      case 64:
        if (left) {
          float distance_tmp_buffer[64];
          for (size_t m = 0; m < M; m++) {
            size_t i = 0;
            for (; i + 64 <= ny; i += 64) {
              krl_L2sqr_continuous_transpose_large_kernel(dis + i, x, y + i * dim, dim);
            }
            krl_L2sqr_continuous_transpose_large_kernel(distance_tmp_buffer, x, y + i * dim, dim);
            size_t remaining_dis_size = dis_size - (m * ny + i);
            if (remaining_dis_size < left) {
              std::printf("Error: UNSAFEMEM in krl_L2sqr_ny_with_handle\n");
              return UNSAFEMEM;
            }
            int ret = SafeMemory::CheckAndMemcpy(
              dis + i, remaining_dis_size * sizeof(float), distance_tmp_buffer, left * sizeof(float));
            if (ret != 0) {
              std::printf("Error: UNSAFEMEM in krl_L2sqr_ny_with_handle\n");
              return UNSAFEMEM;
            }
            dis += ny;
            x += dim;
            y += ceil_ny * dim;
          }
        } else {
          for (size_t m = 0; m < M; m++) {
            for (size_t i = 0; i < ny; i += 64) {
              krl_L2sqr_continuous_transpose_large_kernel(dis + i, x, y + i * dim, dim);
            }
            dis += ny;
            x += dim;
            y += ceil_ny * dim;
          }
        }
        break;
    }
  } else if (kdh->data_bits == 16) {
    // fp16 path not built in minimal KRL for OpenViking
    std::printf("Error: INVALPARAM in krl_L2sqr_ny_with_handle (fp16 not supported)\n");
    return INVALPARAM;
  } else {
    // u8 path not built in minimal KRL for OpenViking
    std::printf("Error: INVALPARAM in krl_L2sqr_ny_with_handle (u8 not supported)\n");
    return INVALPARAM;
  }
  return SUCCESS;
}

}  // extern "C"
