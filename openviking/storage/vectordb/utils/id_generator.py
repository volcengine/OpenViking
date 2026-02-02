# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import os
import random
import threading
import time


class SnowflakeGenerator:
    """
    Distributed unique ID generator based on Twitter's Snowflake algorithm.
    Generates 64-bit integers (int64/uint64 compatible).

    Structure (64 bits):
    - 1 bit: Unused (sign bit)
    - 41 bits: Timestamp (milliseconds since epoch)
    - 10 bits: Machine/Process ID (5 bits datacenter + 5 bits worker)
    - 12 bits: Sequence number (per millisecond)
    """

    # Constants
    EPOCH = 1704067200000  # 2024-01-01 00:00:00 UTC

    worker_id_bits = 5
    datacenter_id_bits = 5
    sequence_bits = 12

    max_worker_id = -1 ^ (-1 << worker_id_bits)
    max_datacenter_id = -1 ^ (-1 << datacenter_id_bits)
    max_sequence = -1 ^ (-1 << sequence_bits)

    worker_id_shift = sequence_bits
    datacenter_id_shift = sequence_bits + worker_id_bits
    timestamp_left_shift = sequence_bits + worker_id_bits + datacenter_id_bits

    def __init__(self, worker_id: int = None, datacenter_id: int = None):
        """
        Initialize the generator.
        If worker_id/datacenter_id are not provided, they are generated based on PID.
        """
        if worker_id is None:
            # Use Process ID to distinguish processes on the same machine
            # PID can be large, so we mask it to fit in worker_id_bits
            worker_id = os.getpid() & self.max_worker_id

        if datacenter_id is None:
            # In a containerized environment, hostname usually changes.
            # Using hash of hostname or a random number if not configured.
            # For local single-node usage, random is acceptable initialization.
            datacenter_id = random.randint(0, self.max_datacenter_id)

        if worker_id > self.max_worker_id or worker_id < 0:
            raise ValueError(f"worker_id must be between 0 and {self.max_worker_id}")
        if datacenter_id > self.max_datacenter_id or datacenter_id < 0:
            raise ValueError(f"datacenter_id must be between 0 and {self.max_datacenter_id}")

        self.worker_id = worker_id
        self.datacenter_id = datacenter_id

        self.sequence = 0
        self.last_timestamp = -1
        self.lock = threading.Lock()

    def _current_timestamp(self):
        return int(time.time() * 1000)

    def next_id(self) -> int:
        """
        Generate the next unique ID.
        """
        with self.lock:
            timestamp = self._current_timestamp()

            if timestamp < self.last_timestamp:
                # Clock moved backwards, refuse to generate id
                # Wait until clock catches up or throw error
                offset = self.last_timestamp - timestamp
                if offset <= 5:  # If offset is small, just wait
                    time.sleep(offset / 1000.0 + 0.001)
                    timestamp = self._current_timestamp()

                if timestamp < self.last_timestamp:
                    raise Exception(
                        f"Clock moved backwards. Refusing to generate id for {self.last_timestamp - timestamp} milliseconds"
                    )

            if self.last_timestamp == timestamp:
                self.sequence = (self.sequence + 1) & self.max_sequence
                if self.sequence == 0:
                    # Sequence exhausted, wait for next millisecond
                    while timestamp <= self.last_timestamp:
                        timestamp = self._current_timestamp()
            else:
                self.sequence = 0

            self.last_timestamp = timestamp

            new_id = (
                ((timestamp - self.EPOCH) << self.timestamp_left_shift)
                | (self.datacenter_id << self.datacenter_id_shift)
                | (self.worker_id << self.worker_id_shift)
                | self.sequence
            )

            return new_id


# Global instance
_default_generator = SnowflakeGenerator()


def generate_auto_id() -> int:
    """
    Generate a globally unique 64-bit integer ID.
    Returns:
        int: A 64-bit unique integer
    """
    return _default_generator.next_id()
