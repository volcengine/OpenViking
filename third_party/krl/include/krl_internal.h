/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
 */

#include "krl.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <arm_neon.h>
#include <math.h>

typedef int64_t idx_t;

/*
 * @brief Handle for batch distance computation.
 * @param metric_type Measurement type (e.g., L2, inner product).
 * @param quanted_scale Quantization scale parameter.
 * @param quanted_bias Quantization bias parameter.
 * @param data_bits Data bit width, supports 8, 16, 32.
 * @param full_data_bits Full data bit width, supports 8, 16, 32. Only used when full_data_bits > data_bits for
 * second-stage rearrangement.
 * @param M Number of query vectors (only for GEMM).
 * @param blocksize Block size for transpose GEMM, supports 16, 32, 64. 0 or 1 indicates using parameters.
 * @param d Dimension of vectors.
 * @param ny Number of base vectors per query.
 * @param ceil_ny Number of base vectors per query (rounded up to blocksize).
 * @param quanted_bytes Size for storing or reading quantized data.
 * @param transposed_bytes Size for storing or reading transposed data.
 * @param quanted_codes Pointer to quantized vector matrix.
 * @param transposed_codes Pointer to transposed codes (only for data_bit=32).
 */
typedef struct KRLBatchDistanceHandle {
    int metric_type;
    float quanted_scale;
    float quanted_bias;
    size_t data_bits;
    size_t full_data_bits;
    size_t M;
    size_t blocksize;
    size_t d;
    size_t ny;
    size_t ceil_ny;
    size_t quanted_bytes;
    size_t transposed_bytes;
    uint8_t *quanted_codes;
    float *transposed_codes;
} KRLDistanceHandle;

/*
 * @brief Handle for 8-bit lookup table.
 * @param use_idx Whether to use index buffer.
 * @param capacity Capacity of the lookup table.
 * @param idx_buffer Index buffer for storing indices.
 * @param distance_buffer Distance buffer for storing distances.
 */
typedef struct KRLLookupTable8bitHandle {
    int use_idx;
    size_t capacity;
    size_t *idx_buffer;
    float *distance_buffer;
} KRLLUT8bHandle;

/* -------------------------------------- L2 Distance Compute -------------------------------------- */
#ifdef __cplusplus
extern "C" {
#endif
/*
 * @brief Compute L2 square distance between vectors.
 * @param dis Output distance array.
 * @param x Pointer to the first vector.
 * @param y Pointer to the second vector.
 * @param ny Number of vectors.
 * @param d Dimension of vectors.
 */
void krl_L2sqr_ny_u8u32(uint32_t *dis, const uint8_t *x, const uint8_t *y, size_t ny, size_t d);

/*
 * @brief Compute the L2 square of a float16 vector with multiple float16 vectors in batches
 * @param dis Pointer to the array storing the computed L2 squares
 * @param x Pointer to the input float16 vector
 * @param y Pointer to the array of float16 vectors
 * @param d The dimension of the vectors
 * @param ny The number of y vectors to process
 */
void krl_L2sqr_ny_f16f16(uint16_t *dis, const uint16_t *x, const uint16_t *y, size_t ny, size_t d);

/*
 * @brief Compute the L2 square of a float16 vector with multiple float16 vectors based on given indices
 * @param dis Pointer to the array storing the computed L2 squares
 * @param x Pointer to the input float16 vector
 * @param y Pointer to the array of float16 vectors
 * @param ids Pointer to the array of indices specifying which y vectors to use
 * @param d The dimension of the vectors
 * @param ny The number of y vectors to process
 */
void krl_L2sqr_by_idx_f16f16(uint16_t *dis, const uint16_t *x, const uint16_t *y,
    const int64_t *ids, /* ids of y vecs */
    size_t d, size_t ny);

/* -------------------------------------- IP Distance Compute -------------------------------------- */

/*
 * @brief Compute inner product between vectors.
 * @param dis Output distance array.
 * @param x Pointer to the first vector.
 * @param y Pointer to the second vector.
 * @param ny Number of vectors.
 * @param d Dimension of vectors.
 */
KRL_API_PUBLIC void krl_inner_product_ny_s8s32(int32_t *dis, const int8_t *x, const int8_t *y, size_t ny, size_t d);

/*
 * @brief Compute the inner product of a float16 vector with multiple float16 vectors based on given indices
 * @param dis Pointer to the array storing the computed inner products
 * @param x Pointer to the input float16 vector
 * @param y Pointer to the array of float16 vectors
 * @param ids Pointer to the array of indices specifying which y vectors to use
 * @param d The dimension of the vectors
 * @param ny The number of y vectors to process
 */
void krl_inner_product_by_idx_f16f16(
    uint16_t *dis, const uint16_t *x, const uint16_t *y, const int64_t *ids, size_t d, size_t ny);

/*
 * @brief Compute the inner product of a float16 vector with multiple float16 vectors in batches
 * @param dis Pointer to the array storing the computed inner products
 * @param x Pointer to the input float16 vector
 * @param y Pointer to the array of float16 vectors
 * @param d The dimension of the vectors
 * @param ny The number of y vectors to process
 */
void krl_inner_product_ny_f16f16(uint16_t *dis, const uint16_t *x, const uint16_t *y, size_t ny, size_t d);

/*
 * @brief Compute the negative inner product distance between a int8 vector and multiple int8 vectors based on indices.
 * @param dis Pointer to the output array storing the computed distances.
 * @param x Pointer to the input int8 vector.
 * @param y Pointer to the input int8 vector array.
 * @param ids Pointer to the indices of the y vectors.
 * @param d Length of the vectors.
 * @param ny Number of vectors to compute.
 */
void krl_negative_inner_product_by_idx_s8f32(float *dis, const int8_t *x, const int8_t *y,
    const int64_t *ids, /* ids of y vecs */
    size_t d, size_t ny);

/* -------------------------------------- 4bits lookup table -------------------------------------- */

/* -------------------------------------- 8bits lookup table -------------------------------------- */

#ifdef __cplusplus
}
#endif
/*
 * @brief Matrix block transpose function.
 * @param src Input matrix.
 * @param ny Number of vectors.
 * @param dim Dimension of vectors.
 * @param blocksize Block size for transpose.
 * @param block Output transposed matrix.
 * @param block_size Length of block.
 */
int krl_matrix_block_transpose(
    const uint32_t *src, size_t ny, size_t dim, size_t blocksize, uint32_t *block, size_t block_size);

/*
 * @brief Lookup table function for 8-bit codes.
 * @param nsq Number of subquantizers.
 * @param ncode Number of codes.
 * @param codes Input codes.
 * @param sim_table Similarity table.
 * @param distance Output distance array.
 * @param dis0 Initial distance value.
 */
void krl_table_lookup_8b_f32_f16(
    size_t nsq, size_t ncode, const uint8_t *codes, const float16_t *sim_table, float *distance, float16_t dis0);

/* -------------------------------------- minmax quant -------------------------------------- */

/*
 * @brief Quantize float to float16.
 * @param src Input float array.
 * @param n Number of elements.
 * @param out Output float16 array.
 */
void quant_f16(const float *src, idx_t n, float16_t *out);

/*
 * @brief Quantize float to uint8.
 * @param src Input float array.
 * @param n Number of elements.
 * @param out Output uint8 array.
 */
void quant_u8(const float *src, idx_t n, uint8_t *out);

/*
 * @brief Quantize float to uint8 with scale and bias.
 * @param src Input float array.
 * @param n Number of elements.
 * @param out Output uint8 array.
 * @param scale Scale factor.
 * @param bias Bias value.
 */
void quant_u8_with_parm(const float *src, idx_t n, uint8_t *out, float scale, float bias);

/*
 * @brief Quantize float to int8.
 * @param src Input float array.
 * @param n Number of elements.
 * @param out Output int8 array.
 */
void quant_s8(const float *src, idx_t n, int8_t *out);

/*
 * @brief Quantize float to int8 with scale.
 * @param src Input float array.
 * @param n Number of elements.
 * @param out Output int8 array.
 * @param scale Scale factor.
 */
void quant_s8_with_parm(const float *src, idx_t n, int8_t *out, float scale);

/*
 * @brief Compute quantization parameters.
 * @param n Number of elements.
 * @param x Input float array.
 * @param metric_type Distance metric type.
 * @param range Quantization range.
 * @param scale Output scale factor.
 * @param bias Output bias value.
 * @return size_t Number of quantization parameters.
 */
size_t compute_quant_parm(idx_t n, const float *x, int metric_type, int range, float *scale, float *bias);

/*
 * @brief Quantize float to uint8 with specific metric type.
 * @param n Number of elements.
 * @param x Input float array.
 * @param out Output uint8 array.
 * @param metric_type Distance metric type.
 * @param use_parm Whether to use parameters.
 * @param scale Scale factor.
 * @param bias Bias value.
 */
void quant_sq8(idx_t n, const float *x, uint8_t *out, int metric_type, int use_parm, float scale, float bias);

/* -------------------------------------- heap sort -------------------------------------- */

/*
 * @brief Obtain top-k elements in descending order using heap sort.
 * @param k Number of top elements.
 * @param distances Distance array.
 * @param k_base Base index for top elements.
 * @param base_distances Base distance array.
 */
void krl_obtion_topk_heap_desc(idx_t k, float *distances, idx_t k_base, const float *base_distances);

/*
 * @brief Obtain top-k elements in ascending order using heap sort.
 * @param k Number of top elements.
 * @param distances Distance array.
 * @param k_base Base index for top elements.
 * @param base_distances Base distance array.
 */
void krl_obtion_topk_heap_asce(idx_t k, float *distances, idx_t k_base, const float *base_distances);

/*
 * @brief Reorder two heaps in descending order.
 * @param k Number of top elements.
 * @param labels Label array.
 * @param distances Distance array.
 * @param k_base Base index for top elements.
 * @param base_labels Base label array.
 * @param base_distances Base distance array.
 */
void krl_reorder_2_heaps_desc(
    idx_t k, idx_t *labels, float *distances, idx_t k_base, const idx_t *base_labels, const float *base_distances);

/*
 * @brief Reorder two heaps in ascending order.
 * @param k Number of top elements.
 * @param labels Label array.
 * @param distances Distance array.
 * @param k_base Base index for top elements.
 * @param base_labels Base label array.
 * @param base_distances Base distance array.
 */
void krl_reorder_2_heaps_asce(
    idx_t k, idx_t *labels, float *distances, idx_t k_base, const idx_t *base_labels, const float *base_distances);

/*
 * @brief Adaptively reorder elements in ascending order.
 * @param dis Distance array.
 * @param label Label array.
 * @param n Number of elements.
 * @param target Target value.
 * @return idx_t Index of the target value.
 */
idx_t Adapt_reorder_asce(float *dis, idx_t *label, idx_t n, float target);

/*
 * @brief Adaptively reorder elements in descending order.
 * @param dis Distance array.
 * @param label Label array.
 * @param n Number of elements.
 * @param target Target value.
 * @return idx_t Index of the target value.
 */
idx_t Adapt_reorder_desc(float *dis, idx_t *label, idx_t n, float target);