# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import json
from dataclasses import dataclass, field
from typing import List

from openviking.storage.vectordb.store.serializable import serializable


@serializable
@dataclass
class CandidateData:
    label: int = 0
    vector: List[float] = field(default_factory=list)
    sparse_raw_terms: List[str] = field(default_factory=list)
    sparse_values: List[float] = field(default_factory=list)
    fields: str = ""
    expire_ns_ts: int = 0

    def __str__(self):
        data_dict = {
            "label": self.label,
            "vector": self.vector,
            "sparse_raw_terms": self.sparse_raw_terms,
            "sparse_values": self.sparse_values,
            "fields": self.fields,
            "expire_ns_ts": self.expire_ns_ts,
        }
        return json.dumps(data_dict)

    def __repr__(self):
        return self.__str__()


@serializable
@dataclass
class DeltaRecord:
    class Type:
        UPSERT = 0
        DELETE = 1

    type: int = 0
    label: int = 0
    vector: List[float] = field(default_factory=list)
    sparse_raw_terms: List[str] = field(default_factory=list)
    sparse_values: List[float] = field(default_factory=list)
    fields: str = ""
    old_fields: str = ""

    def __str__(self):
        data_dict = {
            "type": self.type,
            "label": self.label,
            "vector": self.vector,
            "sparse_raw_terms": self.sparse_raw_terms,
            "sparse_values": self.sparse_values,
            "fields": self.fields,
            "old_fields": self.old_fields,
        }
        return json.dumps(data_dict)

    def __repr__(self):
        return self.__str__()


@serializable
@dataclass
class TTLData:
    label: int = 0
