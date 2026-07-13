# AC-3 Resource Ingestion Design

## Goal

Allow `add_resource` to ingest `.ac3` audio resources from local files and HTTP URLs with the same behavior as existing supported audio formats: download or access the file, validate its format, store the original bytes, and generate basic audio metadata.

## Scope

The feature adds recognition and validation only. It does not decode, transcode, inspect duration/sample rate/channels, or perform speech-to-text transcription.

## Design

### Format registration

Add `.ac3` to the shared `AUDIO_EXTENSIONS` collection. Both `URLTypeDetector.EXTENSION_MAP` and `AudioParser.supported_extensions` are derived from this collection, so this single registration makes explicit `.ac3` HTTP paths route as `DOWNLOAD_AUDIO` and makes local or downloaded `.ac3` files route to `AudioParser`.

### MIME mapping

Add the IANA-registered media type `audio/ac3` to `IANA_MEDIA_TYPE_TO_EXTENSION`, mapped to `.ac3`. This preserves the correct suffix when a server identifies AC-3 through `Content-Type`, including URLs without a filename extension.

### Signature validation

Add `.ac3` to `AudioParser`'s signature table with the two-byte AC-3 synchronization word `0x0B77` defined by RFC 4184. Valid files continue through the existing storage and metadata path. Files with an `.ac3` suffix but a different signature raise the existing `ValueError` used for invalid audio files.

The HTTP magic-byte detector will also recognize `0x0B77` as downloadable AC-3. This lets extensionless URLs using a generic `application/octet-stream` response avoid the webpage crawler and retain an `.ac3` temporary suffix. Because E-AC-3 shares this synchronization word, this detector only establishes the AC-3 family container needed for routing; the feature does not claim codec-level differentiation.

## Data flow

For the reported URL, the `.ac3` suffix is recognized before the HEAD request, so `HTTPAccessor` downloads it as audio and does not invoke `WebImporter`. The downloaded temporary file retains `.ac3`, `ParserRegistry` selects `AudioParser`, and `AudioParser` validates the synchronization word before storing the original bytes and returning the existing basic audio `ParseResult`.

For an extensionless URL, `audio/ac3` response headers or the `0x0B77` signature provide the same routing result.

## Error handling

- Network failures retain the existing `HTTPAccessor` behavior.
- An `.ac3` resource whose bytes do not start with `0x0B77` fails with the existing invalid-audio `ValueError`.
- No fallback to webpage crawling is allowed after AC-3 is recognized by extension, MIME type, or signature.

## Testing

Add focused regression coverage for:

- `.ac3` membership in supported audio extensions and `ParserRegistry` routing to `AudioParser`.
- HTTP URL detection of an explicit `.ac3` path without requiring HEAD metadata.
- MIME mapping from `audio/ac3` to `.ac3`.
- GET magic-byte refinement from generic `application/octet-stream` to `DOWNLOAD_AUDIO` with an `.ac3` suffix.
- `AudioParser` acceptance of `0x0B77` and rejection of an invalid `.ac3` signature.
- The relevant existing HTTP accessor and media parser tests continue to pass.

## Acceptance criteria

- `https://filesamples.com/samples/audio/ac3/sample1.ac3` is routed as an audio download rather than a webpage.
- A valid `.ac3` resource completes the same ingestion path and produces the same class of basic metadata as MP3, WAV, OGG, FLAC, AAC, M4A, and Opus resources.
- Existing supported media formats and webpage ingestion behavior do not regress.
