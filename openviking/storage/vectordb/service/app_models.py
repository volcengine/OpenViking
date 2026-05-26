# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, Optional

from pydantic import BaseModel, Field

# ==================== Collection Models ====================


class CollectionCreateRequest(BaseModel):
    CollectionName: str = Field(..., description="Collection name")
    ProjectName: Optional[str] = Field("default", description="Project name")
    Description: Optional[str] = Field("", description="Collection description")
    Fields: Optional[Any] = Field(None, description="Field definitions")
    Vectorize: Optional[Any] = Field(None, description="Vectorize configuration")


class CollectionUpdateRequest(BaseModel):
    CollectionName: str = Field(..., description="Collection name")
    ProjectName: Optional[str] = Field("default", description="Project name")
    Description: Optional[str] = Field(None, description="Collection description")
    Fields: Optional[Any] = Field(None, description="Field definitions")


class CollectionInfoRequest(BaseModel):
    CollectionName: str = Field(..., description="Collection name")
    ProjectName: Optional[str] = Field("default", description="Project name")


class CollectionListRequest(BaseModel):
    ProjectName: Optional[str] = Field("default", description="Project name")


class CollectionDropRequest(BaseModel):
    CollectionName: str = Field(..., description="Collection name")
    ProjectName: Optional[str] = Field("default", description="Project name")


# ==================== Data Models ====================


class DataUpsertRequest(BaseModel):
    collection_name: str = Field(..., description="Collection name")
    project: Optional[str] = Field("default", description="Project name")
    fields: Any = Field(..., description="Data list")
    ttl: Optional[int] = Field(0, description="Time to live")


class DataFetchRequest(BaseModel):
    collection_name: str = Field(..., description="Collection name")
    project: Optional[str] = Field("default", description="Project name")
    ids: Any = Field(..., description="Primary key list")


class DataDeleteRequest(BaseModel):
    collection_name: str = Field(..., description="Collection name")
    project: Optional[str] = Field("default", description="Project name")
    ids: Optional[Any] = Field(None, description="Primary key list")
    del_all: Optional[bool] = Field(False, description="Delete all flag")


# ==================== Index Models ====================


class IndexCreateRequest(BaseModel):
    CollectionName: str = Field(..., description="Collection name")
    IndexName: str = Field(..., description="Index name")
    ProjectName: Optional[str] = Field("default", description="Project name")
    VectorIndex: Any = Field(..., description="Vector index configuration")
    ScalarIndex: Optional[Any] = Field(None, description="Scalar index configuration")
    Description: Optional[str] = Field(None, description="Index description")


class IndexUpdateRequest(BaseModel):
    CollectionName: str = Field(..., description="Collection name")
    IndexName: str = Field(..., description="Index name")
    ProjectName: Optional[str] = Field("default", description="Project name")
    ScalarIndex: Optional[Any] = Field(None, description="Scalar index configuration")
    Description: Optional[str] = Field(None, description="Index description")


class IndexInfoRequest(BaseModel):
    CollectionName: str = Field(..., description="Collection name")
    IndexName: str = Field(..., description="Index name")
    ProjectName: Optional[str] = Field("default", description="Project name")


class IndexListRequest(BaseModel):
    CollectionName: str = Field(..., description="Collection name")
    ProjectName: Optional[str] = Field("default", description="Project name")


class IndexDropRequest(BaseModel):
    CollectionName: str = Field(..., description="Collection name")
    IndexName: str = Field(..., description="Index name")
    ProjectName: Optional[str] = Field("default", description="Project name")


# ==================== Search Models ====================


class SearchByVectorRequest(BaseModel):
    collection_name: str = Field(..., description="Collection name")
    index_name: str = Field(..., description="Index name")
    project: Optional[str] = Field("default", description="Project name")
    dense_vector: Optional[Any] = Field(None, description="Dense vector")
    sparse_vector: Optional[Any] = Field(None, description="Sparse vector")
    filter: Optional[Any] = Field(None, description="Filter conditions")
    output_fields: Optional[Any] = Field(None, description="Output fields")
    limit: Optional[int] = Field(10, description="Result limit")
    offset: Optional[int] = Field(0, description="Result offset")


class SearchByIdRequest(BaseModel):
    collection_name: str = Field(..., description="Collection name")
    index_name: str = Field(..., description="Index name")
    project: Optional[str] = Field("default", description="Project name")
    id: Any = Field(..., description="ID for search")
    filter: Optional[Any] = Field(None, description="Filter conditions")
    output_fields: Optional[Any] = Field(None, description="Output fields")
    limit: Optional[int] = Field(10, description="Result limit")
    offset: Optional[int] = Field(0, description="Result offset")


class SearchByMultiModalRequest(BaseModel):
    collection_name: str = Field(..., description="Collection name")
    index_name: str = Field(..., description="Index name")
    project: Optional[str] = Field("default", description="Project name")
    text: Optional[str] = Field(None, description="Text for search")
    image: Optional[str] = Field(None, description="Image for search")
    video: Optional[str] = Field(None, description="Video for search")
    filter: Optional[Any] = Field(None, description="Filter conditions")
    output_fields: Optional[Any] = Field(None, description="Output fields")
    limit: Optional[int] = Field(10, description="Result limit")
    offset: Optional[int] = Field(0, description="Result offset")


class SearchByScalarRequest(BaseModel):
    collection_name: str = Field(..., description="Collection name")
    index_name: str = Field(..., description="Index name")
    project: Optional[str] = Field("default", description="Project name")
    field: str = Field(..., description="Field name for sorting")
    order: Optional[str] = Field("desc", description="Sort order (asc/desc)")
    filter: Optional[Any] = Field(None, description="Filter conditions")
    output_fields: Optional[Any] = Field(None, description="Output fields")
    limit: Optional[int] = Field(10, description="Result limit")
    offset: Optional[int] = Field(0, description="Result offset")


class SearchByRandomRequest(BaseModel):
    collection_name: str = Field(..., description="Collection name")
    index_name: str = Field(..., description="Index name")
    project: Optional[str] = Field("default", description="Project name")
    filter: Optional[Any] = Field(None, description="Filter conditions")
    output_fields: Optional[Any] = Field(None, description="Output fields")
    limit: Optional[int] = Field(10, description="Result limit")
    offset: Optional[int] = Field(0, description="Result offset")


class SearchByKeywordsRequest(BaseModel):
    collection_name: str = Field(..., description="Collection name")
    index_name: str = Field(..., description="Index name")
    project: Optional[str] = Field("default", description="Project name")
    keywords: Optional[Any] = Field(None, description="Keywords list")
    query: Optional[str] = Field(None, description="Query string")
    filter: Optional[Any] = Field(None, description="Filter conditions")
    output_fields: Optional[Any] = Field(None, description="Output fields")
    limit: Optional[int] = Field(10, description="Result limit")
    offset: Optional[int] = Field(0, description="Result offset")


# ==================== Response Model ====================


class ApiResponse(BaseModel):
    code: int = Field(..., description="Status code")
    message: str = Field(..., description="Response message")
    data: Optional[Any] = Field(None, description="Response data")
    time_cost: Optional[float] = Field(
        None, description="Time cost in seconds", alias="time_cost(second)"
    )

    class Config:
        populate_by_name = True
