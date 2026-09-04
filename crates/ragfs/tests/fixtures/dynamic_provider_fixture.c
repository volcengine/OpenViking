#include "openviking_cache_provider_v1.h"

#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    const OvCacheHostApiV1 *host;
} FixtureProvider;

static const OvCacheHostApiV1 *fixture_host = NULL;
static _Atomic uint32_t fixture_close_calls = 0;

static int32_t fixture_copy(
    const uint8_t *data,
    size_t len,
    OvCacheOwnedBufferV1 *output) {
    output->data = fixture_host->alloc(len, OV_CACHE_ALIGNOF(uint8_t));
    if (len > 0 && output->data == NULL) {
        return OV_CACHE_STATUS_INTERNAL;
    }
    if (len > 0) {
        memcpy(output->data, data, len);
    }
    output->len = len;
    return OV_CACHE_STATUS_OK;
}

static int32_t fixture_create(
    const OvCacheHostApiV1 *host,
    OvCacheByteSliceV1 params_json,
    void **provider,
    OvCacheOwnedBufferV1 *error) {
    (void)params_json;
    (void)error;
    FixtureProvider *state = (FixtureProvider *)malloc(sizeof(FixtureProvider));
    if (state == NULL) {
        return OV_CACHE_STATUS_INTERNAL;
    }
    state->host = host;
    fixture_host = host;
    *provider = state;
    return OV_CACHE_STATUS_OK;
}

static int32_t fixture_ping(void *provider, OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)error;
    return OV_CACHE_STATUS_OK;
}

static int32_t fixture_close(void *provider, OvCacheOwnedBufferV1 *error) {
    (void)error;
    atomic_fetch_add(&fixture_close_calls, 1);
    free(provider);
#ifdef OV_FIXTURE_CLOSE_ERROR
    return OV_CACHE_STATUS_INTERNAL;
#else
    return OV_CACHE_STATUS_OK;
#endif
}

static int32_t fixture_get(
    void *provider,
    OvCacheByteSliceV1 key,
    OvCacheOptionalOwnedBufferV1 *output,
    OvCacheOwnedBufferV1 *error) {
    (void)provider;
    (void)key;
    (void)error;
    static const uint8_t value[] = "fixture-value";
    output->present = 1;
    int32_t status = fixture_copy(value, sizeof(value) - 1, &output->value);
#ifdef OV_FIXTURE_BAD_LENGTH
    if (status == OV_CACHE_STATUS_OK) {
        output->value.len += 1;
    }
#endif
    return status;
}

OV_CACHE_PROVIDER_EXPORT uint32_t ov_fixture_close_count(void) {
    return atomic_load(&fixture_close_calls);
}

static const OvCacheProviderApiV1 FIXTURE_API = {
#ifdef OV_FIXTURE_BAD_ABI
    .abi_version = OV_CACHE_PROVIDER_ABI_V1 + 1,
#else
    .abi_version = OV_CACHE_PROVIDER_ABI_V1,
#endif
    .struct_size = sizeof(OvCacheProviderApiV1),
    .create = fixture_create,
    .ping = fixture_ping,
    .close = fixture_close,
    .get = fixture_get,
};

OV_CACHE_PROVIDER_EXPORT
const OvCacheProviderApiV1 *openviking_cache_provider_v1(void) {
    return &FIXTURE_API;
}
