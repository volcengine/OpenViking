// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0
// Adapted from KRL (Kunpeng Retrieval Library) for ARM NEON optimizations.

#include "krl.h"
#include "krl_internal.h"
#include "safe_memory.h"
#include "platform_macros.h"
#include <cstdio>

extern "C" {

/*
* @brief Compute the inner product of two float vectors.
* @param x Pointer to the first vector (float).
* @param y Pointer to the second vector (float).
* @param d Dimension of the vectors.
* @param dis Stores the inner product result (float).
* @param dis_size Length of dis.
*/
KRL_IMPRECISE_FUNCTION_BEGIN
int krl_ipdis(const float *x, const float *__restrict y, const size_t d, float *dis, size_t dis_size)
{
  size_t i;
  float res;
  constexpr size_t single_round = 16;

  if (d < 1 || d > 65535) {
    std::printf("Error: INVALPARAM in krl_ipdis\n");
    return INVALPARAM;
  }

  if (x == nullptr || y == nullptr || dis == nullptr || dis_size < 1) {
    std::printf("Error: INVALPOINTER in krl_ipdis\n");
    return INVALPOINTER;
  }

  if (likely(d >= single_round)) {
    float32x4_t x8_0 = vld1q_f32(x);
    float32x4_t x8_1 = vld1q_f32(x + 4);
    float32x4_t x8_2 = vld1q_f32(x + 8);
    float32x4_t x8_3 = vld1q_f32(x + 12);

    float32x4_t y8_0 = vld1q_f32(y);
    float32x4_t y8_1 = vld1q_f32(y + 4);
    float32x4_t y8_2 = vld1q_f32(y + 8);
    float32x4_t y8_3 = vld1q_f32(y + 12);

    float32x4_t d8_0 = vmulq_f32(x8_0, y8_0);
    float32x4_t d8_1 = vmulq_f32(x8_1, y8_1);
    float32x4_t d8_2 = vmulq_f32(x8_2, y8_2);
    float32x4_t d8_3 = vmulq_f32(x8_3, y8_3);

    for (i = single_round; i <= d - single_round; i += single_round) {
      x8_0 = vld1q_f32(x + i);
      y8_0 = vld1q_f32(y + i);
      d8_0 = vmlaq_f32(d8_0, x8_0, y8_0);

      x8_1 = vld1q_f32(x + i + 4);
      y8_1 = vld1q_f32(y + i + 4);
      d8_1 = vmlaq_f32(d8_1, x8_1, y8_1);

      x8_2 = vld1q_f32(x + i + 8);
      y8_2 = vld1q_f32(y + i + 8);
      d8_2 = vmlaq_f32(d8_2, x8_2, y8_2);

      x8_3 = vld1q_f32(x + i + 12);
      y8_3 = vld1q_f32(y + i + 12);
      d8_3 = vmlaq_f32(d8_3, x8_3, y8_3);
    }

    d8_0 = vaddq_f32(d8_0, d8_1);
    d8_2 = vaddq_f32(d8_2, d8_3);
    d8_0 = vaddq_f32(d8_0, d8_2);
    res = vaddvq_f32(d8_0);
  } else {
    i = 0;
    res = 0;
  }

  for (; i < d; i++) {
    const float tmp = x[i] * y[i];
    res += tmp;
  }
  *dis = res;
  return SUCCESS;
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute inner products for two float vectors in batch.
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Output array to store the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_inner_product_batch2(const float *x, const float *__restrict y, const size_t d, float *dis)
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

    float32x4_t d0_0 = vmulq_f32(x_0, y0_0);
    float32x4_t d0_1 = vmulq_f32(x_1, y0_1);
    float32x4_t d1_0 = vmulq_f32(x_0, y1_0);
    float32x4_t d1_1 = vmulq_f32(x_1, y1_1);

    for (i = single_round; i <= d - single_round; i += single_round) {
      x_0 = vld1q_f32(x + i);
      y0_0 = vld1q_f32(y + i);
      y1_0 = vld1q_f32(y + d + i);
      d0_0 = vmlaq_f32(d0_0, x_0, y0_0);
      d1_0 = vmlaq_f32(d1_0, x_0, y1_0);

      x_1 = vld1q_f32(x + i + 4);
      y0_1 = vld1q_f32(y + i + 4);
      y1_1 = vld1q_f32(y + d + i + 4);
      d0_1 = vmlaq_f32(d0_1, x_1, y0_1);
      d1_1 = vmlaq_f32(d1_1, x_1, y1_1);
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
    const float tmp0 = x[i] * *(y + i);
    const float tmp1 = x[i] * *(y + d + i);
    dis[0] += tmp0;
    dis[1] += tmp1;
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute inner products for four float vectors in batch.
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Output array to store the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_inner_product_batch4(const float *x, const float *__restrict y, const size_t d, float *dis)
{
  size_t i;
  constexpr size_t single_round = 4; /* 128/32 */

  if (likely(d >= single_round)) {
    float32x4_t neon_query = vld1q_f32(x);
    float32x4_t neon_base1 = vld1q_f32(y);
    float32x4_t neon_base2 = vld1q_f32(y + d);
    float32x4_t neon_base3 = vld1q_f32(y + 2 * d);
    float32x4_t neon_base4 = vld1q_f32(y + 3 * d);

    float32x4_t neon_res1 = vmulq_f32(neon_base1, neon_query);
    float32x4_t neon_res2 = vmulq_f32(neon_base2, neon_query);
    float32x4_t neon_res3 = vmulq_f32(neon_base3, neon_query);
    float32x4_t neon_res4 = vmulq_f32(neon_base4, neon_query);

    for (i = single_round; i <= d - single_round; i += single_round) {
      neon_query = vld1q_f32(x + i);
      neon_base1 = vld1q_f32(y + i);
      neon_base2 = vld1q_f32(y + d + i);
      neon_base3 = vld1q_f32(y + 2 * d + i);
      neon_base4 = vld1q_f32(y + 3 * d + i);

      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_query);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_query);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_query);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_query);
    }
    dis[0] = vaddvq_f32(neon_res1);
    dis[1] = vaddvq_f32(neon_res2);
    dis[2] = vaddvq_f32(neon_res3);
    dis[3] = vaddvq_f32(neon_res4);
  } else {
    for (int i = 0; i < 4; i++) {
      dis[i] = 0.0f;
    }
    i = 0;
  }
  if (i < d) {
    float d0 = x[i] * *(y + i);
    float d1 = x[i] * *(y + d + i);
    float d2 = x[i] * *(y + 2 * d + i);
    float d3 = x[i] * *(y + 3 * d + i);

    for (i++; i < d; ++i) {
      d0 += x[i] * *(y + i);
      d1 += x[i] * *(y + d + i);
      d2 += x[i] * *(y + 2 * d + i);
      d3 += x[i] * *(y + 3 * d + i);
    }

    dis[0] += d0;
    dis[1] += d1;
    dis[2] += d2;
    dis[3] += d3;
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute inner products for eight float vectors in batch.
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Output array to store the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_inner_product_batch8(const float *x, const float *__restrict y, const size_t d, float *dis)
{
  size_t i;
  constexpr size_t single_round = 4; /* 128/32 */

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

    float32x4_t neon_res1 = vmulq_f32(neon_base1, neon_query);
    float32x4_t neon_res2 = vmulq_f32(neon_base2, neon_query);
    float32x4_t neon_res3 = vmulq_f32(neon_base3, neon_query);
    float32x4_t neon_res4 = vmulq_f32(neon_base4, neon_query);
    float32x4_t neon_res5 = vmulq_f32(neon_base5, neon_query);
    float32x4_t neon_res6 = vmulq_f32(neon_base6, neon_query);
    float32x4_t neon_res7 = vmulq_f32(neon_base7, neon_query);
    float32x4_t neon_res8 = vmulq_f32(neon_base8, neon_query);

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

      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_query);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_query);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_query);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_query);
      neon_res5 = vmlaq_f32(neon_res5, neon_base5, neon_query);
      neon_res6 = vmlaq_f32(neon_res6, neon_base6, neon_query);
      neon_res7 = vmlaq_f32(neon_res7, neon_base7, neon_query);
      neon_res8 = vmlaq_f32(neon_res8, neon_base8, neon_query);
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
    float d0 = x[i] * *(y + i);
    float d1 = x[i] * *(y + d + i);
    float d2 = x[i] * *(y + 2 * d + i);
    float d3 = x[i] * *(y + 3 * d + i);
    float d4 = x[i] * *(y + 4 * d + i);
    float d5 = x[i] * *(y + 5 * d + i);
    float d6 = x[i] * *(y + 6 * d + i);
    float d7 = x[i] * *(y + 7 * d + i);

    for (i++; i < d; ++i) {
      d0 += x[i] * *(y + i);
      d1 += x[i] * *(y + d + i);
      d2 += x[i] * *(y + 2 * d + i);
      d3 += x[i] * *(y + 3 * d + i);
      d4 += x[i] * *(y + 4 * d + i);
      d5 += x[i] * *(y + 5 * d + i);
      d6 += x[i] * *(y + 6 * d + i);
      d7 += x[i] * *(y + 7 * d + i);
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
* @brief Compute inner products for sixteen float vectors in batch.
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Output array to store the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_inner_product_batch16(const float *x, const float *__restrict y, const size_t d, float *dis)
{
  size_t i;
  constexpr size_t single_round = 4; /* 128/32 */

  if (likely(d >= single_round)) {
    /* Load query vector and database vectors */
    float32x4_t neon_query = vld1q_f32(x);
    float32x4_t neon_base1 = vld1q_f32(y);
    float32x4_t neon_base2 = vld1q_f32(y + d);
    float32x4_t neon_base3 = vld1q_f32(y + 2 * d);
    float32x4_t neon_base4 = vld1q_f32(y + 3 * d);
    float32x4_t neon_base5 = vld1q_f32(y + 4 * d);
    float32x4_t neon_base6 = vld1q_f32(y + 5 * d);
    float32x4_t neon_base7 = vld1q_f32(y + 6 * d);
    float32x4_t neon_base8 = vld1q_f32(y + 7 * d);

    /* Compute initial inner products */
    float32x4_t neon_res1 = vmulq_f32(neon_base1, neon_query);
    float32x4_t neon_res2 = vmulq_f32(neon_base2, neon_query);
    float32x4_t neon_res3 = vmulq_f32(neon_base3, neon_query);
    float32x4_t neon_res4 = vmulq_f32(neon_base4, neon_query);
    float32x4_t neon_res5 = vmulq_f32(neon_base5, neon_query);
    float32x4_t neon_res6 = vmulq_f32(neon_base6, neon_query);
    float32x4_t neon_res7 = vmulq_f32(neon_base7, neon_query);
    float32x4_t neon_res8 = vmulq_f32(neon_base8, neon_query);

    /* Load additional database vectors  */
    neon_base1 = vld1q_f32(y + 8 * d);
    neon_base2 = vld1q_f32(y + 9 * d);
    neon_base3 = vld1q_f32(y + 10 * d);
    neon_base4 = vld1q_f32(y + 11 * d);
    neon_base5 = vld1q_f32(y + 12 * d);
    neon_base6 = vld1q_f32(y + 13 * d);
    neon_base7 = vld1q_f32(y + 14 * d);
    neon_base8 = vld1q_f32(y + 15 * d);

    /* Compute additional inner products */
    float32x4_t neon_res9 = vmulq_f32(neon_base1, neon_query);
    float32x4_t neon_res10 = vmulq_f32(neon_base2, neon_query);
    float32x4_t neon_res11 = vmulq_f32(neon_base3, neon_query);
    float32x4_t neon_res12 = vmulq_f32(neon_base4, neon_query);
    float32x4_t neon_res13 = vmulq_f32(neon_base5, neon_query);
    float32x4_t neon_res14 = vmulq_f32(neon_base6, neon_query);
    float32x4_t neon_res15 = vmulq_f32(neon_base7, neon_query);
    float32x4_t neon_res16 = vmulq_f32(neon_base8, neon_query);

    for (i = single_round; i <= d - single_round; i += single_round) {
      /* Update query and database vectors */
      neon_query = vld1q_f32(x + i);
      neon_base1 = vld1q_f32(y + i);
      neon_base2 = vld1q_f32(y + d + i);
      neon_base3 = vld1q_f32(y + 2 * d + i);
      neon_base4 = vld1q_f32(y + 3 * d + i);
      neon_base5 = vld1q_f32(y + 4 * d + i);
      neon_base6 = vld1q_f32(y + 5 * d + i);
      neon_base7 = vld1q_f32(y + 6 * d + i);
      neon_base8 = vld1q_f32(y + 7 * d + i);

      /* Update inner products for first 8 vectors */
      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_query);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_query);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_query);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_query);
      neon_res5 = vmlaq_f32(neon_res5, neon_base5, neon_query);
      neon_res6 = vmlaq_f32(neon_res6, neon_base6, neon_query);
      neon_res7 = vmlaq_f32(neon_res7, neon_base7, neon_query);
      neon_res8 = vmlaq_f32(neon_res8, neon_base8, neon_query);

      /* Update database vectors for additional 8 vectors */
      neon_base1 = vld1q_f32(y + 8 * d + i);
      neon_base2 = vld1q_f32(y + 9 * d + i);
      neon_base3 = vld1q_f32(y + 10 * d + i);
      neon_base4 = vld1q_f32(y + 11 * d + i);
      neon_base5 = vld1q_f32(y + 12 * d + i);
      neon_base6 = vld1q_f32(y + 13 * d + i);
      neon_base7 = vld1q_f32(y + 14 * d + i);
      neon_base8 = vld1q_f32(y + 15 * d + i);

      /* Update inner products for additional 8 vectors */
      neon_res9 = vmlaq_f32(neon_res9, neon_base1, neon_query);
      neon_res10 = vmlaq_f32(neon_res10, neon_base2, neon_query);
      neon_res11 = vmlaq_f32(neon_res11, neon_base3, neon_query);
      neon_res12 = vmlaq_f32(neon_res12, neon_base4, neon_query);
      neon_res13 = vmlaq_f32(neon_res13, neon_base5, neon_query);
      neon_res14 = vmlaq_f32(neon_res14, neon_base6, neon_query);
      neon_res15 = vmlaq_f32(neon_res15, neon_base7, neon_query);
      neon_res16 = vmlaq_f32(neon_res16, neon_base8, neon_query);
    }

    /* Store results for all 16 vectors */
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
    /* Initialize results to zero if dimension is less than single_round */
    for (int i = 0; i < 16; i++) {
      dis[i] = 0.0f;
    }
    i = 0;
  }

  /* Handle remaining elements if dimension is not a multiple of single_round */
  if (i < d) {
    float d0 = x[i] * *(y + i);
    float d1 = x[i] * *(y + d + i);
    float d2 = x[i] * *(y + 2 * d + i);
    float d3 = x[i] * *(y + 3 * d + i);
    float d4 = x[i] * *(y + 4 * d + i);
    float d5 = x[i] * *(y + 5 * d + i);
    float d6 = x[i] * *(y + 6 * d + i);
    float d7 = x[i] * *(y + 7 * d + i);
    float d8 = x[i] * *(y + 8 * d + i);
    float d9 = x[i] * *(y + 9 * d + i);
    float d10 = x[i] * *(y + 10 * d + i);
    float d11 = x[i] * *(y + 11 * d + i);
    float d12 = x[i] * *(y + 12 * d + i);
    float d13 = x[i] * *(y + 13 * d + i);
    float d14 = x[i] * *(y + 14 * d + i);
    float d15 = x[i] * *(y + 15 * d + i);

    for (i++; i < d; ++i) {
      d0 += x[i] * *(y + i);
      d1 += x[i] * *(y + d + i);
      d2 += x[i] * *(y + 2 * d + i);
      d3 += x[i] * *(y + 3 * d + i);
      d4 += x[i] * *(y + 4 * d + i);
      d5 += x[i] * *(y + 5 * d + i);
      d6 += x[i] * *(y + 6 * d + i);
      d7 += x[i] * *(y + 7 * d + i);
      d8 += x[i] * *(y + 8 * d + i);
      d9 += x[i] * *(y + 9 * d + i);
      d10 += x[i] * *(y + 10 * d + i);
      d11 += x[i] * *(y + 11 * d + i);
      d12 += x[i] * *(y + 12 * d + i);
      d13 += x[i] * *(y + 13 * d + i);
      d14 += x[i] * *(y + 14 * d + i);
      d15 += x[i] * *(y + 15 * d + i);
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
* @brief Compute inner products for two vectors with float precision and store results in dis array.
* @param x Pointer to the query vector (float).
* @param y0 Pointer to the first database vector (float).
* @param y1 Pointer to the second database vector (float).
* @param d Dimension of the vectors.
* @param dis Pointer to the output array for storing the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_inner_product_idx_batch2(
  const float *x, const float *__restrict y0, const float *__restrict y1, const size_t d, float *dis)
{
  size_t i;
  constexpr size_t single_round = 8;

  if (likely(d >= single_round)) {
    float32x4_t x_0 = vld1q_f32(x);
    float32x4_t x_1 = vld1q_f32(x + 4);

    float32x4_t y0_0 = vld1q_f32(y0);
    float32x4_t y0_1 = vld1q_f32(y0 + 4);
    float32x4_t y1_0 = vld1q_f32(y1);
    float32x4_t y1_1 = vld1q_f32(y1 + 4);

    float32x4_t d0_0 = vmulq_f32(x_0, y0_0);
    float32x4_t d0_1 = vmulq_f32(x_1, y0_1);
    float32x4_t d1_0 = vmulq_f32(x_0, y1_0);
    float32x4_t d1_1 = vmulq_f32(x_1, y1_1);
    for (i = single_round; i <= d - single_round; i += single_round) {
      x_0 = vld1q_f32(x + i);
      y0_0 = vld1q_f32(y0 + i);
      y1_0 = vld1q_f32(y1 + i);
      d0_0 = vmlaq_f32(d0_0, x_0, y0_0);
      d1_0 = vmlaq_f32(d1_0, x_0, y1_0);

      x_1 = vld1q_f32(x + i + 4);
      y0_1 = vld1q_f32(y0 + i + 4);
      y1_1 = vld1q_f32(y1 + i + 4);
      d0_1 = vmlaq_f32(d0_1, x_1, y0_1);
      d1_1 = vmlaq_f32(d1_1, x_1, y1_1);
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
    const float tmp0 = x[i] * y0[i];
    const float tmp1 = x[i] * y1[i];
    dis[0] += tmp0;
    dis[1] += tmp1;
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute inner products for four vectors with float precision and store results in dis array.
* @param x Pointer to the query vector (float).
* @param y Array of pointers to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Pointer to the output array for storing the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_inner_product_idx_batch4(const float *x, const float *__restrict *y, const size_t d, float *dis)
{
  size_t i;
  constexpr size_t single_round = 4; /* 128/32 */
  if (likely(d >= single_round)) {
    float32x4_t neon_query = vld1q_f32(x);
    float32x4_t neon_base1 = vld1q_f32(y[0]);
    float32x4_t neon_base2 = vld1q_f32(y[1]);
    float32x4_t neon_base3 = vld1q_f32(y[2]);
    float32x4_t neon_base4 = vld1q_f32(y[3]);

    float32x4_t neon_res1 = vmulq_f32(neon_base1, neon_query);
    float32x4_t neon_res2 = vmulq_f32(neon_base2, neon_query);
    float32x4_t neon_res3 = vmulq_f32(neon_base3, neon_query);
    float32x4_t neon_res4 = vmulq_f32(neon_base4, neon_query);

    for (i = single_round; i <= d - single_round; i += single_round) {
      neon_query = vld1q_f32(x + i);
      neon_base1 = vld1q_f32(y[0] + i);
      neon_base2 = vld1q_f32(y[1] + i);
      neon_base3 = vld1q_f32(y[2] + i);
      neon_base4 = vld1q_f32(y[3] + i);

      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_query);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_query);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_query);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_query);
    }
    dis[0] = vaddvq_f32(neon_res1);
    dis[1] = vaddvq_f32(neon_res2);
    dis[2] = vaddvq_f32(neon_res3);
    dis[3] = vaddvq_f32(neon_res4);
  } else {
    for (int i = 0; i < 4; i++) {
      dis[i] = 0.0f;
    }
    i = 0;
  }
  if (i < d) {
    float d0 = x[i] * *(y[0] + i);
    float d1 = x[i] * *(y[1] + i);
    float d2 = x[i] * *(y[2] + i);
    float d3 = x[i] * *(y[3] + i);

    for (i++; i < d; ++i) {
      d0 += x[i] * *(y[0] + i);
      d1 += x[i] * *(y[1] + i);
      d2 += x[i] * *(y[2] + i);
      d3 += x[i] * *(y[3] + i);
    }

    dis[0] += d0;
    dis[1] += d1;
    dis[2] += d2;
    dis[3] += d3;
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute inner products for eight vectors with float precision and store results in dis array.
* @param x Pointer to the query vector (float).
* @param y Array of pointers to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Pointer to the output array for storing the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_inner_product_idx_batch8(const float *x, const float *__restrict *y, const size_t d, float *dis)
{
  size_t i;
  constexpr size_t single_round = 4; /* 128/32 */
  if (likely(d >= single_round)) {
    float32x4_t neon_query = vld1q_f32(x);
    float32x4_t neon_base1 = vld1q_f32(y[0]);
    float32x4_t neon_base2 = vld1q_f32(y[1]);
    float32x4_t neon_base3 = vld1q_f32(y[2]);
    float32x4_t neon_base4 = vld1q_f32(y[3]);
    float32x4_t neon_base5 = vld1q_f32(y[4]);
    float32x4_t neon_base6 = vld1q_f32(y[5]);
    float32x4_t neon_base7 = vld1q_f32(y[6]);
    float32x4_t neon_base8 = vld1q_f32(y[7]);

    float32x4_t neon_res1 = vmulq_f32(neon_base1, neon_query);
    float32x4_t neon_res2 = vmulq_f32(neon_base2, neon_query);
    float32x4_t neon_res3 = vmulq_f32(neon_base3, neon_query);
    float32x4_t neon_res4 = vmulq_f32(neon_base4, neon_query);
    float32x4_t neon_res5 = vmulq_f32(neon_base5, neon_query);
    float32x4_t neon_res6 = vmulq_f32(neon_base6, neon_query);
    float32x4_t neon_res7 = vmulq_f32(neon_base7, neon_query);
    float32x4_t neon_res8 = vmulq_f32(neon_base8, neon_query);
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

      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_query);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_query);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_query);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_query);
      neon_res5 = vmlaq_f32(neon_res5, neon_base5, neon_query);
      neon_res6 = vmlaq_f32(neon_res6, neon_base6, neon_query);
      neon_res7 = vmlaq_f32(neon_res7, neon_base7, neon_query);
      neon_res8 = vmlaq_f32(neon_res8, neon_base8, neon_query);
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
    float d0 = x[i] * *(y[0] + i);
    float d1 = x[i] * *(y[1] + i);
    float d2 = x[i] * *(y[2] + i);
    float d3 = x[i] * *(y[3] + i);
    float d4 = x[i] * *(y[4] + i);
    float d5 = x[i] * *(y[5] + i);
    float d6 = x[i] * *(y[6] + i);
    float d7 = x[i] * *(y[7] + i);
    for (i++; i < d; ++i) {
      d0 += x[i] * *(y[0] + i);
      d1 += x[i] * *(y[1] + i);
      d2 += x[i] * *(y[2] + i);
      d3 += x[i] * *(y[3] + i);
      d4 += x[i] * *(y[4] + i);
      d5 += x[i] * *(y[5] + i);
      d6 += x[i] * *(y[6] + i);
      d7 += x[i] * *(y[7] + i);
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
* @brief Compute inner products for sixteen vectors with indices and prefetch optimization.
* @param x Pointer to the query vector (float).
* @param y Array of pointers to the database vectors (float).
* @param d Dimension of the vectors.
* @param dis Pointer to the output array for storing the results (float).
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_inner_product_idx_prefetch_batch16(
  const float *x, const float *__restrict *y, const size_t d, float *dis)
{
  size_t i;
  constexpr size_t single_round = 4; /* 128 / 8 */
  constexpr size_t multi_round = 32; /* 8 * single_round */
  if (d >= multi_round) {
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
    float32x4_t neon_res1, neon_res2, neon_res3, neon_res4;
    float32x4_t neon_res5, neon_res6, neon_res7, neon_res8;
    float32x4_t neon_res9, neon_res10, neon_res11, neon_res12;
    float32x4_t neon_res13, neon_res14, neon_res15, neon_res16;
    {
      const float32x4_t neon_query = vld1q_f32(x);
      float32x4_t neon_base1 = vld1q_f32(y[0]);
      float32x4_t neon_base2 = vld1q_f32(y[1]);
      float32x4_t neon_base3 = vld1q_f32(y[2]);
      float32x4_t neon_base4 = vld1q_f32(y[3]);
      float32x4_t neon_base5 = vld1q_f32(y[4]);
      float32x4_t neon_base6 = vld1q_f32(y[5]);
      float32x4_t neon_base7 = vld1q_f32(y[6]);
      float32x4_t neon_base8 = vld1q_f32(y[7]);

      neon_res1 = vmulq_f32(neon_base1, neon_query);
      neon_res2 = vmulq_f32(neon_base2, neon_query);
      neon_res3 = vmulq_f32(neon_base3, neon_query);
      neon_res4 = vmulq_f32(neon_base4, neon_query);
      neon_res5 = vmulq_f32(neon_base5, neon_query);
      neon_res6 = vmulq_f32(neon_base6, neon_query);
      neon_res7 = vmulq_f32(neon_base7, neon_query);
      neon_res8 = vmulq_f32(neon_base8, neon_query);

      neon_base1 = vld1q_f32(y[8]);
      neon_base2 = vld1q_f32(y[9]);
      neon_base3 = vld1q_f32(y[10]);
      neon_base4 = vld1q_f32(y[11]);
      neon_base5 = vld1q_f32(y[12]);
      neon_base6 = vld1q_f32(y[13]);
      neon_base7 = vld1q_f32(y[14]);
      neon_base8 = vld1q_f32(y[15]);

      neon_res9 = vmulq_f32(neon_base1, neon_query);
      neon_res10 = vmulq_f32(neon_base2, neon_query);
      neon_res11 = vmulq_f32(neon_base3, neon_query);
      neon_res12 = vmulq_f32(neon_base4, neon_query);
      neon_res13 = vmulq_f32(neon_base5, neon_query);
      neon_res14 = vmulq_f32(neon_base6, neon_query);
      neon_res15 = vmulq_f32(neon_base7, neon_query);
      neon_res16 = vmulq_f32(neon_base8, neon_query);
    }
    for (i = single_round; i < multi_round; i += single_round) {
      const float32x4_t neon_query = vld1q_f32(x + i);
      float32x4_t neon_base1 = vld1q_f32(y[0] + i);
      float32x4_t neon_base2 = vld1q_f32(y[1] + i);
      float32x4_t neon_base3 = vld1q_f32(y[2] + i);
      float32x4_t neon_base4 = vld1q_f32(y[3] + i);
      float32x4_t neon_base5 = vld1q_f32(y[4] + i);
      float32x4_t neon_base6 = vld1q_f32(y[5] + i);
      float32x4_t neon_base7 = vld1q_f32(y[6] + i);
      float32x4_t neon_base8 = vld1q_f32(y[7] + i);

      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_query);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_query);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_query);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_query);
      neon_res5 = vmlaq_f32(neon_res5, neon_base5, neon_query);
      neon_res6 = vmlaq_f32(neon_res6, neon_base6, neon_query);
      neon_res7 = vmlaq_f32(neon_res7, neon_base7, neon_query);
      neon_res8 = vmlaq_f32(neon_res8, neon_base8, neon_query);

      neon_base1 = vld1q_f32(y[8] + i);
      neon_base2 = vld1q_f32(y[9] + i);
      neon_base3 = vld1q_f32(y[10] + i);
      neon_base4 = vld1q_f32(y[11] + i);
      neon_base5 = vld1q_f32(y[12] + i);
      neon_base6 = vld1q_f32(y[13] + i);
      neon_base7 = vld1q_f32(y[14] + i);
      neon_base8 = vld1q_f32(y[15] + i);

      neon_res9 = vmlaq_f32(neon_res9, neon_base1, neon_query);
      neon_res10 = vmlaq_f32(neon_res10, neon_base2, neon_query);
      neon_res11 = vmlaq_f32(neon_res11, neon_base3, neon_query);
      neon_res12 = vmlaq_f32(neon_res12, neon_base4, neon_query);
      neon_res13 = vmlaq_f32(neon_res13, neon_base5, neon_query);
      neon_res14 = vmlaq_f32(neon_res14, neon_base6, neon_query);
      neon_res15 = vmlaq_f32(neon_res15, neon_base7, neon_query);
      neon_res16 = vmlaq_f32(neon_res16, neon_base8, neon_query);
    }
    for (; i < d - multi_round; i += multi_round) {
      prefetch_L1(x + multi_round + i);
      prefetch_Lx(y[0] + multi_round + i);
      prefetch_Lx(y[1] + multi_round + i);
      prefetch_Lx(y[2] + multi_round + i);
      prefetch_Lx(y[3] + multi_round + i);
      prefetch_Lx(y[4] + multi_round + i);
      prefetch_Lx(y[5] + multi_round + i);
      prefetch_Lx(y[6] + multi_round + i);
      prefetch_Lx(y[7] + multi_round + i);
      prefetch_Lx(y[8] + multi_round + i);
      prefetch_Lx(y[9] + multi_round + i);
      prefetch_Lx(y[10] + multi_round + i);
      prefetch_Lx(y[11] + multi_round + i);
      prefetch_Lx(y[12] + multi_round + i);
      prefetch_Lx(y[13] + multi_round + i);
      prefetch_Lx(y[14] + multi_round + i);
      prefetch_Lx(y[15] + multi_round + i);
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

        neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_query);
        neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_query);
        neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_query);
        neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_query);
        neon_res5 = vmlaq_f32(neon_res5, neon_base5, neon_query);
        neon_res6 = vmlaq_f32(neon_res6, neon_base6, neon_query);
        neon_res7 = vmlaq_f32(neon_res7, neon_base7, neon_query);
        neon_res8 = vmlaq_f32(neon_res8, neon_base8, neon_query);

        neon_base1 = vld1q_f32(y[8] + i + j);
        neon_base2 = vld1q_f32(y[9] + i + j);
        neon_base3 = vld1q_f32(y[10] + i + j);
        neon_base4 = vld1q_f32(y[11] + i + j);
        neon_base5 = vld1q_f32(y[12] + i + j);
        neon_base6 = vld1q_f32(y[13] + i + j);
        neon_base7 = vld1q_f32(y[14] + i + j);
        neon_base8 = vld1q_f32(y[15] + i + j);

        neon_res9 = vmlaq_f32(neon_res9, neon_base1, neon_query);
        neon_res10 = vmlaq_f32(neon_res10, neon_base2, neon_query);
        neon_res11 = vmlaq_f32(neon_res11, neon_base3, neon_query);
        neon_res12 = vmlaq_f32(neon_res12, neon_base4, neon_query);
        neon_res13 = vmlaq_f32(neon_res13, neon_base5, neon_query);
        neon_res14 = vmlaq_f32(neon_res14, neon_base6, neon_query);
        neon_res15 = vmlaq_f32(neon_res15, neon_base7, neon_query);
        neon_res16 = vmlaq_f32(neon_res16, neon_base8, neon_query);
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

      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_query);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_query);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_query);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_query);
      neon_res5 = vmlaq_f32(neon_res5, neon_base5, neon_query);
      neon_res6 = vmlaq_f32(neon_res6, neon_base6, neon_query);
      neon_res7 = vmlaq_f32(neon_res7, neon_base7, neon_query);
      neon_res8 = vmlaq_f32(neon_res8, neon_base8, neon_query);

      neon_base1 = vld1q_f32(y[8] + i);
      neon_base2 = vld1q_f32(y[9] + i);
      neon_base3 = vld1q_f32(y[10] + i);
      neon_base4 = vld1q_f32(y[11] + i);
      neon_base5 = vld1q_f32(y[12] + i);
      neon_base6 = vld1q_f32(y[13] + i);
      neon_base7 = vld1q_f32(y[14] + i);
      neon_base8 = vld1q_f32(y[15] + i);

      neon_res9 = vmlaq_f32(neon_res9, neon_base1, neon_query);
      neon_res10 = vmlaq_f32(neon_res10, neon_base2, neon_query);
      neon_res11 = vmlaq_f32(neon_res11, neon_base3, neon_query);
      neon_res12 = vmlaq_f32(neon_res12, neon_base4, neon_query);
      neon_res13 = vmlaq_f32(neon_res13, neon_base5, neon_query);
      neon_res14 = vmlaq_f32(neon_res14, neon_base6, neon_query);
      neon_res15 = vmlaq_f32(neon_res15, neon_base7, neon_query);
      neon_res16 = vmlaq_f32(neon_res16, neon_base8, neon_query);
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

    float32x4_t neon_res1 = vmulq_f32(neon_base1, neon_query);
    float32x4_t neon_res2 = vmulq_f32(neon_base2, neon_query);
    float32x4_t neon_res3 = vmulq_f32(neon_base3, neon_query);
    float32x4_t neon_res4 = vmulq_f32(neon_base4, neon_query);
    float32x4_t neon_res5 = vmulq_f32(neon_base5, neon_query);
    float32x4_t neon_res6 = vmulq_f32(neon_base6, neon_query);
    float32x4_t neon_res7 = vmulq_f32(neon_base7, neon_query);
    float32x4_t neon_res8 = vmulq_f32(neon_base8, neon_query);

    neon_base1 = vld1q_f32(y[8]);
    neon_base2 = vld1q_f32(y[9]);
    neon_base3 = vld1q_f32(y[10]);
    neon_base4 = vld1q_f32(y[11]);
    neon_base5 = vld1q_f32(y[12]);
    neon_base6 = vld1q_f32(y[13]);
    neon_base7 = vld1q_f32(y[14]);
    neon_base8 = vld1q_f32(y[15]);

    float32x4_t neon_res9 = vmulq_f32(neon_base1, neon_query);
    float32x4_t neon_res10 = vmulq_f32(neon_base2, neon_query);
    float32x4_t neon_res11 = vmulq_f32(neon_base3, neon_query);
    float32x4_t neon_res12 = vmulq_f32(neon_base4, neon_query);
    float32x4_t neon_res13 = vmulq_f32(neon_base5, neon_query);
    float32x4_t neon_res14 = vmulq_f32(neon_base6, neon_query);
    float32x4_t neon_res15 = vmulq_f32(neon_base7, neon_query);
    float32x4_t neon_res16 = vmulq_f32(neon_base8, neon_query);
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

      neon_res1 = vmlaq_f32(neon_res1, neon_base1, neon_query);
      neon_res2 = vmlaq_f32(neon_res2, neon_base2, neon_query);
      neon_res3 = vmlaq_f32(neon_res3, neon_base3, neon_query);
      neon_res4 = vmlaq_f32(neon_res4, neon_base4, neon_query);
      neon_res5 = vmlaq_f32(neon_res5, neon_base5, neon_query);
      neon_res6 = vmlaq_f32(neon_res6, neon_base6, neon_query);
      neon_res7 = vmlaq_f32(neon_res7, neon_base7, neon_query);
      neon_res8 = vmlaq_f32(neon_res8, neon_base8, neon_query);

      neon_base1 = vld1q_f32(y[8] + i);
      neon_base2 = vld1q_f32(y[9] + i);
      neon_base3 = vld1q_f32(y[10] + i);
      neon_base4 = vld1q_f32(y[11] + i);
      neon_base5 = vld1q_f32(y[12] + i);
      neon_base6 = vld1q_f32(y[13] + i);
      neon_base7 = vld1q_f32(y[14] + i);
      neon_base8 = vld1q_f32(y[15] + i);

      neon_res9 = vmlaq_f32(neon_res9, neon_base1, neon_query);
      neon_res10 = vmlaq_f32(neon_res10, neon_base2, neon_query);
      neon_res11 = vmlaq_f32(neon_res11, neon_base3, neon_query);
      neon_res12 = vmlaq_f32(neon_res12, neon_base4, neon_query);
      neon_res13 = vmlaq_f32(neon_res13, neon_base5, neon_query);
      neon_res14 = vmlaq_f32(neon_res14, neon_base6, neon_query);
      neon_res15 = vmlaq_f32(neon_res15, neon_base7, neon_query);
      neon_res16 = vmlaq_f32(neon_res16, neon_base8, neon_query);
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
    float d0 = x[i] * *(y[0] + i);
    float d1 = x[i] * *(y[1] + i);
    float d2 = x[i] * *(y[2] + i);
    float d3 = x[i] * *(y[3] + i);
    float d4 = x[i] * *(y[4] + i);
    float d5 = x[i] * *(y[5] + i);
    float d6 = x[i] * *(y[6] + i);
    float d7 = x[i] * *(y[7] + i);
    float d8 = x[i] * *(y[8] + i);
    float d9 = x[i] * *(y[9] + i);
    float d10 = x[i] * *(y[10] + i);
    float d11 = x[i] * *(y[11] + i);
    float d12 = x[i] * *(y[12] + i);
    float d13 = x[i] * *(y[13] + i);
    float d14 = x[i] * *(y[14] + i);
    float d15 = x[i] * *(y[15] + i);
    for (i++; i < d; ++i) {
      d0 += x[i] * *(y[0] + i);
      d1 += x[i] * *(y[1] + i);
      d2 += x[i] * *(y[2] + i);
      d3 += x[i] * *(y[3] + i);
      d4 += x[i] * *(y[4] + i);
      d5 += x[i] * *(y[5] + i);
      d6 += x[i] * *(y[6] + i);
      d7 += x[i] * *(y[7] + i);
      d8 += x[i] * *(y[8] + i);
      d9 += x[i] * *(y[9] + i);
      d10 += x[i] * *(y[10] + i);
      d11 += x[i] * *(y[11] + i);
      d12 += x[i] * *(y[12] + i);
      d13 += x[i] * *(y[13] + i);
      d14 += x[i] * *(y[14] + i);
      d15 += x[i] * *(y[15] + i);
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
* @brief Compute inner products for 16 vectors with float precision and store results in dis array.
* @param dis Pointer to the output array for storing the results (float).
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_inner_product_continuous_transpose_large_kernel(
  float *dis, const float *x, const float *y, const size_t d)
{
  float32x4_t neon_res[16];
  float32x4_t single_query = vdupq_n_f32(x[0]);

  float32x4_t neon_base1 = vld1q_f32(y);
  float32x4_t neon_base2 = vld1q_f32(y + 4);
  float32x4_t neon_base3 = vld1q_f32(y + 8);
  float32x4_t neon_base4 = vld1q_f32(y + 12);
  float32x4_t neon_base5 = vld1q_f32(y + 16);
  float32x4_t neon_base6 = vld1q_f32(y + 20);
  float32x4_t neon_base7 = vld1q_f32(y + 24);
  float32x4_t neon_base8 = vld1q_f32(y + 28);

  neon_res[0] = vmulq_f32(neon_base1, single_query);
  neon_res[1] = vmulq_f32(neon_base2, single_query);
  neon_res[2] = vmulq_f32(neon_base3, single_query);
  neon_res[3] = vmulq_f32(neon_base4, single_query);
  neon_res[4] = vmulq_f32(neon_base5, single_query);
  neon_res[5] = vmulq_f32(neon_base6, single_query);
  neon_res[6] = vmulq_f32(neon_base7, single_query);
  neon_res[7] = vmulq_f32(neon_base8, single_query);

  neon_base1 = vld1q_f32(y + 32);
  neon_base2 = vld1q_f32(y + 36);
  neon_base3 = vld1q_f32(y + 40);
  neon_base4 = vld1q_f32(y + 44);
  neon_base5 = vld1q_f32(y + 48);
  neon_base6 = vld1q_f32(y + 52);
  neon_base7 = vld1q_f32(y + 56);
  neon_base8 = vld1q_f32(y + 60);

  neon_res[8] = vmulq_f32(neon_base1, single_query);
  neon_res[9] = vmulq_f32(neon_base2, single_query);
  neon_res[10] = vmulq_f32(neon_base3, single_query);
  neon_res[11] = vmulq_f32(neon_base4, single_query);
  neon_res[12] = vmulq_f32(neon_base5, single_query);
  neon_res[13] = vmulq_f32(neon_base6, single_query);
  neon_res[14] = vmulq_f32(neon_base7, single_query);
  neon_res[15] = vmulq_f32(neon_base8, single_query);

  /* dim loop */
  for (size_t i = 1; i < d; ++i) {
    single_query = vdupq_n_f32(x[i]);
    neon_base1 = vld1q_f32(y + 64 * i);
    neon_base2 = vld1q_f32(y + 64 * i + 4);
    neon_base3 = vld1q_f32(y + 64 * i + 8);
    neon_base4 = vld1q_f32(y + 64 * i + 12);
    neon_base5 = vld1q_f32(y + 64 * i + 16);
    neon_base6 = vld1q_f32(y + 64 * i + 20);
    neon_base7 = vld1q_f32(y + 64 * i + 24);
    neon_base8 = vld1q_f32(y + 64 * i + 28);

    neon_res[0] = vmlaq_f32(neon_res[0], neon_base1, single_query);
    neon_res[1] = vmlaq_f32(neon_res[1], neon_base2, single_query);
    neon_res[2] = vmlaq_f32(neon_res[2], neon_base3, single_query);
    neon_res[3] = vmlaq_f32(neon_res[3], neon_base4, single_query);
    neon_res[4] = vmlaq_f32(neon_res[4], neon_base5, single_query);
    neon_res[5] = vmlaq_f32(neon_res[5], neon_base6, single_query);
    neon_res[6] = vmlaq_f32(neon_res[6], neon_base7, single_query);
    neon_res[7] = vmlaq_f32(neon_res[7], neon_base8, single_query);

    neon_base1 = vld1q_f32(y + 64 * i + 32);
    neon_base2 = vld1q_f32(y + 64 * i + 36);
    neon_base3 = vld1q_f32(y + 64 * i + 40);
    neon_base4 = vld1q_f32(y + 64 * i + 44);
    neon_base5 = vld1q_f32(y + 64 * i + 48);
    neon_base6 = vld1q_f32(y + 64 * i + 52);
    neon_base7 = vld1q_f32(y + 64 * i + 56);
    neon_base8 = vld1q_f32(y + 64 * i + 60);

    neon_res[8] = vmlaq_f32(neon_res[8], neon_base1, single_query);
    neon_res[9] = vmlaq_f32(neon_res[9], neon_base2, single_query);
    neon_res[10] = vmlaq_f32(neon_res[10], neon_base3, single_query);
    neon_res[11] = vmlaq_f32(neon_res[11], neon_base4, single_query);
    neon_res[12] = vmlaq_f32(neon_res[12], neon_base5, single_query);
    neon_res[13] = vmlaq_f32(neon_res[13], neon_base6, single_query);
    neon_res[14] = vmlaq_f32(neon_res[14], neon_base7, single_query);
    neon_res[15] = vmlaq_f32(neon_res[15], neon_base8, single_query);
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
* @brief Compute inner products for 8 vectors with float precision and store results in dis array.
* @param dis Pointer to the output array for storing the results (float).
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_inner_product_continuous_transpose_medium_kernel(
  float *dis, const float *x, const float *y, const size_t d)
{
  float32x4_t neon_res[8];
  float32x4_t single_query = vdupq_n_f32(x[0]);
  float32x4_t neon_base1 = vld1q_f32(y);
  float32x4_t neon_base2 = vld1q_f32(y + 4);
  float32x4_t neon_base3 = vld1q_f32(y + 8);
  float32x4_t neon_base4 = vld1q_f32(y + 12);
  float32x4_t neon_base5 = vld1q_f32(y + 16);
  float32x4_t neon_base6 = vld1q_f32(y + 20);
  float32x4_t neon_base7 = vld1q_f32(y + 24);
  float32x4_t neon_base8 = vld1q_f32(y + 28);

  neon_res[0] = vmulq_f32(neon_base1, single_query);
  neon_res[1] = vmulq_f32(neon_base2, single_query);
  neon_res[2] = vmulq_f32(neon_base3, single_query);
  neon_res[3] = vmulq_f32(neon_base4, single_query);
  neon_res[4] = vmulq_f32(neon_base5, single_query);
  neon_res[5] = vmulq_f32(neon_base6, single_query);
  neon_res[6] = vmulq_f32(neon_base7, single_query);
  neon_res[7] = vmulq_f32(neon_base8, single_query);

  /* dim loop */
  for (size_t i = 1; i < d; ++i) {
    single_query = vdupq_n_f32(x[i]);
    neon_base1 = vld1q_f32(y + 32 * i);
    neon_base2 = vld1q_f32(y + 32 * i + 4);
    neon_base3 = vld1q_f32(y + 32 * i + 8);
    neon_base4 = vld1q_f32(y + 32 * i + 12);
    neon_base5 = vld1q_f32(y + 32 * i + 16);
    neon_base6 = vld1q_f32(y + 32 * i + 20);
    neon_base7 = vld1q_f32(y + 32 * i + 24);
    neon_base8 = vld1q_f32(y + 32 * i + 28);

    neon_res[0] = vmlaq_f32(neon_res[0], neon_base1, single_query);
    neon_res[1] = vmlaq_f32(neon_res[1], neon_base2, single_query);
    neon_res[2] = vmlaq_f32(neon_res[2], neon_base3, single_query);
    neon_res[3] = vmlaq_f32(neon_res[3], neon_base4, single_query);
    neon_res[4] = vmlaq_f32(neon_res[4], neon_base5, single_query);
    neon_res[5] = vmlaq_f32(neon_res[5], neon_base6, single_query);
    neon_res[6] = vmlaq_f32(neon_res[6], neon_base7, single_query);
    neon_res[7] = vmlaq_f32(neon_res[7], neon_base8, single_query);
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
  }
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute inner products for 4 vectors with float precision and store results in dis array.
* @param dis Pointer to the output array for storing the results (float).
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param d Dimension of the vectors.
*/
KRL_IMPRECISE_FUNCTION_BEGIN
static void krl_inner_product_continuous_transpose_mini_kernel(
  float *dis, const float *x, const float *y, const size_t d)
{
  float32x4_t neon_res[4];
  float32x4_t single_query = vdupq_n_f32(x[0]);
  float32x4_t neon_base1 = vld1q_f32(y);
  float32x4_t neon_base2 = vld1q_f32(y + 4);
  float32x4_t neon_base3 = vld1q_f32(y + 8);
  float32x4_t neon_base4 = vld1q_f32(y + 12);

  neon_res[0] = vmulq_f32(neon_base1, single_query);
  neon_res[1] = vmulq_f32(neon_base2, single_query);
  neon_res[2] = vmulq_f32(neon_base3, single_query);
  neon_res[3] = vmulq_f32(neon_base4, single_query);

  /* dim loop */
  for (size_t i = 1; i < d; ++i) {
    single_query = vdupq_n_f32(x[i]);
    neon_base1 = vld1q_f32(y + 16 * i);
    neon_base2 = vld1q_f32(y + 16 * i + 4);
    neon_base3 = vld1q_f32(y + 16 * i + 8);
    neon_base4 = vld1q_f32(y + 16 * i + 12);

    neon_res[0] = vmlaq_f32(neon_res[0], neon_base1, single_query);
    neon_res[1] = vmlaq_f32(neon_res[1], neon_base2, single_query);
    neon_res[2] = vmlaq_f32(neon_res[2], neon_base3, single_query);
    neon_res[3] = vmlaq_f32(neon_res[3], neon_base4, single_query);
  }

  vst1q_f32(dis, neon_res[0]);
  vst1q_f32(dis + 4, neon_res[1]);
  vst1q_f32(dis + 8, neon_res[2]);
  vst1q_f32(dis + 12, neon_res[3]);
}
KRL_IMPRECISE_FUNCTION_END

/*
* @brief Compute inner products for a batch of vectors based on given indices.
* @param dis Pointer to the output array for storing the results (float).
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param ids Pointer to the indices array for selecting database vectors.
* @param d Dimension of the vectors.
* @param ny Number of database vectors to process.
* @param dis_size Length of dis.
*/
int krl_inner_product_by_idx(
  float *dis, const float *x, const float *y, const int64_t *ids, size_t d, size_t ny, size_t dis_size)
{
  size_t i = 0;
  const float *__restrict listy[16];

  if (d < 1 || d > 65535 || ny < 1 || ny > 1ULL << 30) {
    std::printf("Error: INVALPARAM in krl_inner_product_by_idx\n");
    return INVALPARAM;
  }

  if (x == nullptr || y == nullptr || ids == nullptr || dis == nullptr || dis_size < ny) {
    std::printf("Error: INVALPOINTER in krl_inner_product_by_idx\n");
    return INVALPOINTER;
  }

  for (; i + 16 <= ny; i += 16) {
    /* Prefetch data for better cache utilization */
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
    krl_inner_product_idx_prefetch_batch16(x, listy, d, dis + i);
  }
  if (ny & 8) {
    listy[0] = (const float *)(y + *(ids + i) * d);
    listy[1] = (const float *)(y + *(ids + i + 1) * d);
    listy[2] = (const float *)(y + *(ids + i + 2) * d);
    listy[3] = (const float *)(y + *(ids + i + 3) * d);
    listy[4] = (const float *)(y + *(ids + i + 4) * d);
    listy[5] = (const float *)(y + *(ids + i + 5) * d);
    listy[6] = (const float *)(y + *(ids + i + 6) * d);
    listy[7] = (const float *)(y + *(ids + i + 7) * d);
    krl_inner_product_idx_batch8(x, listy, d, dis + i);
    i += 8;
  }
  if (ny & 4) {
    listy[0] = (const float *)(y + *(ids + i) * d);
    listy[1] = (const float *)(y + *(ids + i + 1) * d);
    listy[2] = (const float *)(y + *(ids + i + 2) * d);
    listy[3] = (const float *)(y + *(ids + i + 3) * d);
    krl_inner_product_idx_batch4(x, listy, d, dis + i);
    i += 4;
  }
  if (ny & 2) {
    const float *y0 = y + *(ids + i) * d;
    const float *y1 = y + *(ids + i + 1) * d;
    krl_inner_product_idx_batch2(x, y0, y1, d, dis + i);
    i += 2;
  }
  if (ny & 1) {
    krl_ipdis(x, y + d * ids[i], d, &dis[i], 1);
  }
  return SUCCESS;
}

/*
* @brief Compute inner products for a batch of vectors.
* @param dis Pointer to the output array for storing the results (float).
* @param x Pointer to the query vector (float).
* @param y Pointer to the database vectors (float).
* @param ny Number of database vectors to process.
* @param d Dimension of the vectors.
*/
int krl_inner_product_ny(float *dis, const float *x, const float *y, const size_t ny, const size_t d, size_t dis_size)
{
  size_t i = 0;

  if (d < 1 || d > 65535 || ny < 1 || ny > 1ULL << 30) {
    std::printf("Error: INVALPARAM in krl_inner_product_ny\n");
    return INVALPARAM;
  }

  if (x == nullptr || y == nullptr || dis == nullptr || dis_size < ny) {
    std::printf("Error: INVALPOINTER in krl_inner_product_ny\n");
    return INVALPOINTER;
  }

  for (; i + 16 <= ny; i += 16) {
    krl_inner_product_batch16(x, y + i * d, d, dis + i);
  }
  if (ny & 8) {
    krl_inner_product_batch8(x, y + i * d, d, dis + i);
    i += 8;
  }
  if (ny & 4) {
    krl_inner_product_batch4(x, y + i * d, d, dis + i);
    i += 4;
  }
  if (ny & 2) {
    krl_inner_product_batch2(x, y + i * d, d, dis + i);
  }
  if (ny & 1) {
    krl_ipdis(x, y + (ny - 1) * d, d, &dis[ny - 1], 1);
  }
  return SUCCESS;
}

/*
* @brief Compute inner products for a batch of vectors with a given handle.
* @param kdh Pointer to the distance handle containing configuration and data.
* @param dis Pointer to the output array for storing the results (float).
* @param x Pointer to the query vector (float).
* @param dis_size Length of dis.
* @param x_size Length of x.
*/
int krl_inner_product_ny_with_handle(
  const KRLDistanceHandle *kdh, float *dis, const float *x, size_t dis_size, size_t x_size)
{
  if (kdh == nullptr || dis == nullptr || x == nullptr) {
    std::printf("Error: INVALPOINTER in krl_inner_product_ny_with_handle\n");
    return INVALPOINTER;
  }
  const size_t ny = kdh->ny;
  const size_t dim = kdh->d;
  const size_t M = kdh->M;
  if (dis_size < M * ny || x_size < dim * M) {
    std::printf("Error: INVALPARAM in krl_inner_product_ny_with_handle\n");
    return INVALPARAM;
  }

  if (kdh->data_bits == 32) {
    const size_t ceil_ny = kdh->ceil_ny;
    const float *y = (const float *)kdh->transposed_codes;
    const size_t left = ny & (kdh->blocksize - 1);
    switch (kdh->blocksize) {
      case 16:
        if (left) {
          float distance_tmp_buffer[16];
          for (size_t m = 0; m < M; m++) {
            size_t i = 0;
            for (; i + 16 <= ny; i += 16) {
              krl_inner_product_continuous_transpose_mini_kernel(dis + i, x, y + i * dim, dim);
            }
            krl_inner_product_continuous_transpose_mini_kernel(distance_tmp_buffer, x, y + i * dim, dim);

            size_t remaining_dis_size = dis_size - (m * ny + i);
            if (remaining_dis_size < left) {
              std::printf("Error: UNSAFEMEM in krl_inner_product_ny_with_handle\n");
              return UNSAFEMEM;
            }
            int ret = SafeMemory::CheckAndMemcpy(
              dis + i, remaining_dis_size * sizeof(float), distance_tmp_buffer, left * sizeof(float));
            if (ret != 0) {
              std::printf("Error: UNSAFEMEM in krl_inner_product_ny_with_handle\n");
              return UNSAFEMEM;
            }
            dis += ny;
            x += dim;
            y += ceil_ny * dim;
          }
        } else {
          for (size_t m = 0; m < M; m++) {
            for (size_t i = 0; i < ny; i += 16) {
              krl_inner_product_continuous_transpose_mini_kernel(dis + i, x, y + i * dim, dim);
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
              krl_inner_product_continuous_transpose_medium_kernel(dis + i, x, y + i * dim, dim);
            }
            krl_inner_product_continuous_transpose_medium_kernel(distance_tmp_buffer, x, y + i * dim, dim);
            size_t remaining_dis_size = dis_size - (m * ny + i);
            if (remaining_dis_size < left) {
              std::printf("Error: UNSAFEMEM in krl_inner_product_ny_with_handle\n");
              return UNSAFEMEM;
            }
            int ret = SafeMemory::CheckAndMemcpy(
              dis + i, remaining_dis_size * sizeof(float), distance_tmp_buffer, left * sizeof(float));
            if (ret != 0) {
              std::printf("Error: UNSAFEMEM in krl_inner_product_ny_with_handle\n");
              return UNSAFEMEM;
            }
            dis += ny;
            x += dim;
            y += ceil_ny * dim;
          }
        } else {
          for (size_t m = 0; m < M; m++) {
            for (size_t i = 0; i < ny; i += 32) {
              krl_inner_product_continuous_transpose_medium_kernel(dis + i, x, y + i * dim, dim);
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
              krl_inner_product_continuous_transpose_large_kernel(dis + i, x, y + i * dim, dim);
            }
            krl_inner_product_continuous_transpose_large_kernel(distance_tmp_buffer, x, y + i * dim, dim);
            size_t remaining_dis_size = dis_size - (m * ny + i);
            if (remaining_dis_size < left) {
              std::printf("Error: UNSAFEMEM in krl_inner_product_ny_with_handle\n");
              return UNSAFEMEM;
            }
            int ret = SafeMemory::CheckAndMemcpy(
              dis + i, remaining_dis_size * sizeof(float), distance_tmp_buffer, left * sizeof(float));
            if (ret != 0) {
              std::printf("Error: UNSAFEMEM in krl_inner_product_ny_with_handle\n");
              return UNSAFEMEM;
            }
            dis += ny;
            x += dim;
            y += ceil_ny * dim;
          }
        } else {
          for (size_t m = 0; m < M; m++) {
            for (size_t i = 0; i < ny; i += 64) {
              krl_inner_product_continuous_transpose_large_kernel(dis + i, x, y + i * dim, dim);
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
    std::printf("Error: INVALPARAM in krl_inner_product_ny_with_handle (fp16 not supported)\n");
    return INVALPARAM;
  } else {
    // int8 path not built in minimal KRL for OpenViking
    std::printf("Error: INVALPARAM in krl_inner_product_ny_with_handle (int8 not supported)\n");
    return INVALPARAM;
  }
  return SUCCESS;
}

}  // extern "C"
