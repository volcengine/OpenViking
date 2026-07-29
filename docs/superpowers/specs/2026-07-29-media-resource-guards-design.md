# Media Resource Guards Design

## Goal

Fix two resource-control gaps in audio/video understanding:

1. `media.max_concurrent` must limit the whole media operation, including remote
   reads and temporary-file staging, rather than only the provider request.
2. Media whose storage metadata omits a usable size must still be stopped at the
   512 MiB hard limit while it is streamed to disk.

The Ark processing-poll retry behavior identified separately is out of scope.

## Concurrency Design

`VLMConfig.get_media_completion_async()` remains the owner of the existing
per-event-loop media semaphore. It will accept an optional asynchronous staging
callback. After acquiring one `media.max_concurrent` permit, it will:

1. invoke the callback to stream the media into the temporary file;
2. invoke the configured media backend with that file;
3. release the permit after either operation completes or raises.

The media parser will create the temporary-file path, pass a callback that runs
the existing chunked storage read, and then request media completion. This gives
one permit a single, non-reentrant lifetime covering staging through inference.
Direct callers that do not provide a callback retain their current behavior and
are still limited around the provider request.

The general semantic semaphore remains unchanged. It continues to limit DAG
work, while the media semaphore supplies the narrower resource limit required by
large audio/video inputs.

## Size-Limit Design

The 512 MiB media limit will be defined once in a lightweight shared module and
used by both:

- provider capability checks based on storage metadata; and
- the streaming temporary-file writer.

The writer will count bytes after every chunk and reject the media as soon as
the cumulative byte count exceeds the hard limit, regardless of whether
`stat.size` is positive, zero, or absent.

When metadata contains a positive expected size, the writer will also preserve
its existing consistency check and reject a stream that exceeds that declared
size. A zero or absent size means “unknown,” not “empty”; it permits streaming
only up to the independent hard limit.

## Error Handling and Cleanup

Limit violations continue through the parser's existing unsupported-media
handling and produce no generated summary. The backend is not called after a
staging failure.

The existing `NamedTemporaryFile` context remains responsible for cleanup.
Because staging and provider invocation run inside both the temporary-file
context and the media permit, exceptions release the permit and remove the
temporary file.

## Test Strategy

Tests will be written before the implementation changes:

- Start more media tasks than `media.max_concurrent` while the general semantic
  semaphore is larger, block storage reads, and assert that no more than the
  configured number enter staging.
- Return an unknown metadata size, stream beyond a test-sized hard limit, and
  assert that staging stops and the media backend is never called.
- Keep the existing capability, parser, and configuration tests as regressions
  for known sizes and direct media-completion callers.

## Compatibility and Non-Goals

The staging callback is optional, so existing callers of
`get_media_completion_async()` do not need to change. No configuration schema
or default values change.

This fix does not change provider polling, retry classification, media-format
support, prompt generation, or the current fail-soft behavior for unsupported
media.
