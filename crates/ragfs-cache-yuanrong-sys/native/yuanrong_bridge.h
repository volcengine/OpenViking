#ifndef OPENVIKING_YUANRONG_BRIDGE_H
#define OPENVIKING_YUANRONG_BRIDGE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct YrClientHandle YrClientHandle;

typedef struct YrBuffer {
    uint8_t *data;
    size_t len;
    uint8_t found;
} YrBuffer;

enum YrStatus {
    YR_OK = 0,
    YR_NOT_FOUND = 1,
    YR_INVALID_ARGUMENT = 2,
    YR_UNAVAILABLE = 3,
    YR_TIMEOUT = 4,
    YR_INTERNAL = 5,
};

int yr_client_create(const char *host, uint16_t port, int32_t connect_timeout_ms,
                     int32_t request_timeout_ms, YrClientHandle **out);
int yr_client_health_check(YrClientHandle *client);
int yr_client_get(YrClientHandle *client, const uint8_t *key, size_t key_len,
                  uint8_t **data, size_t *size);
int yr_client_set(YrClientHandle *client, const uint8_t *key, size_t key_len,
                  const uint8_t *data, size_t size);
int yr_client_delete(YrClientHandle *client, const uint8_t *key, size_t key_len);
int yr_client_exists(YrClientHandle *client, const uint8_t *key, size_t key_len,
                     uint8_t *exists);
int yr_client_mget(YrClientHandle *client, const uint8_t *const *keys,
                   const size_t *key_lens, size_t count, YrBuffer **values);
int yr_client_mset(YrClientHandle *client, const uint8_t *const *keys,
                   const size_t *key_lens, const uint8_t *const *values,
                   const size_t *value_lens, size_t count);
int yr_client_mdelete(YrClientHandle *client, const uint8_t *const *keys,
                      const size_t *key_lens, size_t count);
int yr_client_shutdown(YrClientHandle *client);
void yr_client_destroy(YrClientHandle *client);
void yr_buffer_free(void *data);
void yr_buffers_free(YrBuffer *values, size_t count);
const char *yr_last_error(YrClientHandle *client);

#ifdef __cplusplus
}
#endif

#endif
