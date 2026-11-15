"""QueueFS v1 enqueue envelope; metadata stays outside the application payload."""

import struct
from uuid import uuid4


def validate_message_keys(group_key: str | None, serial_key: str | None) -> None:
    if group_key is not None and serial_key is not None:
        raise ValueError("group_key and serial_key are mutually exclusive")
    for key in (group_key, serial_key):
        if key is not None and (not isinstance(key, str) or not 1 <= len(key.encode()) <= 4096):
            raise ValueError("message keys must contain 1..4096 UTF-8 bytes")


def encode_enqueue(
    payload: bytes, group_key: str | None, serial_key: str | None
) -> tuple[str, bytes]:
    validate_message_keys(group_key, serial_key)
    message_id = uuid4()
    kind, key = (2, group_key) if group_key is not None else (3, serial_key)
    value = key.encode() if key is not None else b""
    tlv = struct.pack("!HHI", kind, 1, len(value)) + value if key is not None else b""
    header = struct.pack("!4sHHI16s", b"QFSE", 1, 0, 28 + len(tlv), message_id.bytes)
    return str(message_id), header + tlv + payload
