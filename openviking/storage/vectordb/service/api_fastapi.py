# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import os
import time
from dataclasses import asdict
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request

from openviking.storage.vectordb.project.project_group import get_or_create_project_group
from openviking.storage.vectordb.service.app_models import (
    ApiResponse,
    CollectionCreateRequest,
    CollectionDropRequest,
    CollectionUpdateRequest,
    DataDeleteRequest,
    DataUpsertRequest,
    IndexCreateRequest,
    IndexDropRequest,
    IndexUpdateRequest,
    SearchByIdRequest,
    SearchByKeywordsRequest,
    SearchByMultiModalRequest,
    SearchByRandomRequest,
    SearchByScalarRequest,
    SearchByVectorRequest,
)
from openviking.storage.vectordb.service.code import ErrorCode
from openviking.storage.vectordb.utils import data_utils
from openviking_cli.utils.logger import default_logger as logger


# Helper functions for responses
def success_response(message: str, data: Any = None, request: Request = None) -> dict:
    """Create a success response"""
    response = {
        "code": ErrorCode.NO_ERROR.value,
        "message": message,
        "data": data if data is not None else {},
    }

    # Add time cost if available from request state
    if request and hasattr(request.state, "start_time"):
        time_cost = time.time() - request.state.start_time
        response["time_cost(second)"] = round(time_cost, 6)

    return response


def error_response(message: str, code: int, data: Any = None, request: Request = None) -> dict:
    """Create an error response"""
    response = {"code": code, "message": message, "data": data if data is not None else {}}

    # Add time cost if available from request state
    if request and hasattr(request.state, "start_time"):
        time_cost = time.time() - request.state.start_time
        response["time_cost(second)"] = round(time_cost, 6)

    return response


class VikingDBException(Exception):
    def __init__(self, code: ErrorCode, message: str):
        self.code = code
        self.message = message


# ==================== Configuration ====================
PERSIST_PATH = os.environ.get("VIKINGDB_PERSIST_PATH", "./vikingdb_data/")

logger.info(
    f"Initializing ProjectGroup with path: {PERSIST_PATH if PERSIST_PATH else 'volatile mode'}"
)
project_group = get_or_create_project_group(path=PERSIST_PATH)
logger.info("ProjectGroup initialized successfully")

# Create routers
collection_router = APIRouter(prefix="", tags=["Collection"])
data_router = APIRouter(prefix="/api/vikingdb/data", tags=["Data"])
index_router = APIRouter(prefix="", tags=["Index"])
search_router = APIRouter(prefix="/api/vikingdb/data/search", tags=["Search"])


# ==================== Dependencies ====================


def get_project(project_name: str = "default"):
    """Get project instance"""
    return project_group.get_or_create_project(project_name)


def get_collection_or_raise(collection_name: str, project_name: str = "default"):
    """Get collection instance or raise exception if not found"""
    if not collection_name:
        raise VikingDBException(ErrorCode.INVALID_PARAM, "collection name is empty")

    project = project_group.get_or_create_project(project_name)
    collection = project.get_collection(collection_name)
    if not collection:
        raise VikingDBException(ErrorCode.COLLECTION_NOT_EXIST, "collection not exist")
    return collection


# Dependency for GET requests using Query parameters
def get_collection_dependency(
    CollectionName: str = Query(..., description="Collection name"),
    ProjectName: Optional[str] = Query("default", description="Project name"),
):
    if not CollectionName:
        raise VikingDBException(ErrorCode.INVALID_PARAM, "collection name is empty")
    return get_collection_or_raise(CollectionName, ProjectName)


# Dependency for snake_case query params
def get_collection_dependency_snake(
    collection_name: str = Query(..., description="Collection name"),
    project: Optional[str] = Query("default", description="Project name"),
):
    if not collection_name:
        raise VikingDBException(ErrorCode.INVALID_PARAM, "collection name is empty")
    return get_collection_or_raise(collection_name, project)


# ==================== Collection APIs ====================


@collection_router.post("/CreateVikingdbCollection", response_model=ApiResponse)
async def create_collection(request: CollectionCreateRequest, req: Request):
    """Create a new collection"""
    collection_name = request.CollectionName
    if not collection_name:
        return error_response("CollectionName is empty", ErrorCode.INVALID_PARAM.value, request=req)

    project_name = request.ProjectName or "default"
    description = request.Description or ""
    fields = data_utils.convert_dict(request.Fields)
    vectorize = data_utils.convert_dict(request.Vectorize)

    project = get_project(project_name)

    if project.has_collection(collection_name):
        return error_response("collection exist", ErrorCode.COLLECTION_EXIST.value, request=req)

    meta_data = {
        "ProjectName": project_name,
        "CollectionName": collection_name,
        "Description": description,
        "Fields": fields,
        "Vectorize": vectorize,
    }

    logger.info(f"Creating collection: {collection_name} in project: {project_name}")
    logger.debug(f"Collection meta_data: {meta_data}")

    try:
        project.create_collection(collection_name, meta_data)
        logger.info(f"Collection created successfully: {collection_name}")
        return success_response("create collection success", request=req)
    except Exception as e:
        logger.error(f"Failed to create collection: {e}")
        return error_response(str(e), ErrorCode.INTERNAL_ERR.value, request=req)


@collection_router.post("/UpdateVikingdbCollection", response_model=ApiResponse)
async def update_collection(request: CollectionUpdateRequest, req: Request):
    """Update an existing collection"""
    try:
        if not request.CollectionName:
            return error_response(
                "CollectionName is empty", ErrorCode.INVALID_PARAM.value, request=req
            )
        collection = get_collection_or_raise(
            request.CollectionName, request.ProjectName or "default"
        )
        description = request.Description
        fields = data_utils.convert_dict(request.Fields)
        collection.update(fields, description)
        return success_response("update collection success", request=req)
    except VikingDBException as e:
        return error_response(e.message, e.code.value, request=req)


@collection_router.get("/GetVikingdbCollection", response_model=ApiResponse)
async def get_collection_info(req: Request, collection: Any = Depends(get_collection_dependency)):
    """Get collection information"""
    meta_data = collection.get_meta_data()
    return success_response("collection info", meta_data, request=req)


@collection_router.get("/ListVikingdbCollection", response_model=ApiResponse)
async def list_collections(
    req: Request, ProjectName: Optional[str] = Query("default", description="Project name")
):
    """List all collections"""
    project = get_project(ProjectName)
    collection_list = project.list_collections()
    return success_response("collection list", collection_list, request=req)


@collection_router.post("/DeleteVikingdbCollection", response_model=ApiResponse)
async def drop_collection(request: CollectionDropRequest, req: Request):
    """Delete a collection"""
    if not request.CollectionName:
        return error_response(
            "collection name is empty", ErrorCode.INVALID_PARAM.value, request=req
        )

    project_name = request.ProjectName or "default"
    project = get_project(project_name)
    # Check if exists before deleting? The original code didn't check existence explicitly before calling drop,
    # but drop_collection usually handles it or we should check to provide better error.
    # Original code:
    # project = project_group.get_or_create_project(project_name)
    # project.drop_collection(collection_name)
    # Let's keep it simple or verify.
    # If we want to return "collection not exist" we should check.
    # The original code did NOT check if collection exists, it just dropped it.
    # Assuming idempotent or underlying raises.
    try:
        project.drop_collection(request.CollectionName)
        return success_response("drop collection success", request=req)
    except Exception as e:
        # Catch potential errors
        return error_response(str(e), ErrorCode.INTERNAL_ERR.value, request=req)


# ==================== Data APIs ====================


@data_router.post("/upsert", response_model=ApiResponse)
async def upsert_data(request: DataUpsertRequest, req: Request):
    """Upsert data to collection"""
    try:
        collection = get_collection_or_raise(request.collection_name, request.project or "default")

        ttl = request.ttl or 0
        data_list = data_utils.convert_dict(request.fields)

        logger.debug(f"Upserting {len(data_list)} records to {request.collection_name}")
        result = collection.upsert_data(data_list=data_list, ttl=ttl)
        if not result or not result.ids:
            return error_response("upsert data err", ErrorCode.INTERNAL_ERR.value, request=req)

        return success_response("upsert data success", result.ids, request=req)
    except VikingDBException as e:
        return error_response(e.message, e.code.value, request=req)


@data_router.get("/fetch_in_collection", response_model=ApiResponse)
async def fetch_data(
    req: Request,
    ids: str = Query(..., description="Primary key list"),
    collection: Any = Depends(get_collection_dependency_snake),
):
    """Fetch data from collection"""
    primary_keys = data_utils.convert_dict(ids)
    data = collection.fetch_data(primary_keys=primary_keys)
    return success_response("fetch data success", asdict(data), request=req)


@data_router.post("/delete", response_model=ApiResponse)
async def delete_data(request: DataDeleteRequest, req: Request):
    """Delete data from collection"""
    try:
        collection = get_collection_or_raise(request.collection_name, request.project or "default")

        primary_keys = data_utils.convert_dict(request.ids) if request.ids else []
        del_all = request.del_all or False

        if del_all:
            collection.delete_all_data()
            return success_response("del data success", {"deleted": "all"}, request=req)
        else:
            collection.delete_data(primary_keys=primary_keys)
            return success_response("del data success", {"deleted": len(primary_keys)}, request=req)
    except VikingDBException as e:
        return error_response(e.message, e.code.value, request=req)


# ==================== Index APIs ====================


@index_router.post("/CreateVikingdbIndex", response_model=ApiResponse)
async def create_index(request: IndexCreateRequest, req: Request):
    """Create an index"""
    try:
        collection = get_collection_or_raise(
            request.CollectionName, request.ProjectName or "default"
        )

        index_name = request.IndexName
        if not index_name:
            return error_response("index name is empty", ErrorCode.INVALID_PARAM.value, request=req)

        vector_index = data_utils.convert_dict(request.VectorIndex)
        scalar_index = data_utils.convert_dict(request.ScalarIndex)
        if not scalar_index:
            scalar_index = []
        description = request.Description

        meta_data = {
            "IndexName": index_name,
            "VectorIndex": vector_index,
            "ScalarIndex": scalar_index,
        }
        if description:
            meta_data["Description"] = description

        logger.info(f"Creating index: {index_name} in collection: {request.CollectionName}")
        collection.create_index(index_name, meta_data)
        return success_response("create index success", request=req)
    except VikingDBException as e:
        return error_response(e.message, e.code.value, request=req)


@index_router.post("/UpdateVikingdbIndex", response_model=ApiResponse)
async def update_index(request: IndexUpdateRequest, req: Request):
    """Update an index"""
    try:
        collection = get_collection_or_raise(
            request.CollectionName, request.ProjectName or "default"
        )

        index_name = request.IndexName
        if not index_name:
            return error_response("index name is empty", ErrorCode.INVALID_PARAM.value, request=req)

        scalar_index = data_utils.convert_dict(request.ScalarIndex)
        description = request.Description

        collection.update_index(index_name, scalar_index, description)
        return success_response("update index success", request=req)
    except VikingDBException as e:
        return error_response(e.message, e.code.value, request=req)


@index_router.get("/GetVikingdbIndex", response_model=ApiResponse)
async def get_index_info(
    req: Request,
    IndexName: str = Query(..., description="Index name"),
    collection: Any = Depends(get_collection_dependency),
):
    """Get index information"""
    if not IndexName:
        return error_response("index name is empty", ErrorCode.INVALID_PARAM.value, request=req)

    data = collection.get_index_meta_data(IndexName)
    return success_response("get index meta data success", data, request=req)


@index_router.get("/ListVikingdbIndex", response_model=ApiResponse)
async def list_indexes(req: Request, collection: Any = Depends(get_collection_dependency)):
    """List all indexes"""
    data = collection.list_indexes()
    return success_response("list indexes success", data, request=req)


@index_router.post("/DeleteVikingdbIndex", response_model=ApiResponse)
async def drop_index(request: IndexDropRequest, req: Request):
    """Delete an index"""
    try:
        collection = get_collection_or_raise(
            request.CollectionName, request.ProjectName or "default"
        )

        index_name = request.IndexName
        if not index_name:
            return error_response("index name is empty", ErrorCode.INVALID_PARAM.value, request=req)

        collection.drop_index(index_name)
        return success_response("drop index success", request=req)
    except VikingDBException as e:
        return error_response(e.message, e.code.value, request=req)


# ==================== Search APIs ====================


@search_router.post("/vector", response_model=ApiResponse)
async def search_by_vector(request: SearchByVectorRequest, req: Request):
    """Search by vector"""
    try:
        collection = get_collection_or_raise(request.collection_name, request.project or "default")

        index_name = request.index_name
        if not index_name:
            return error_response("index name is empty", ErrorCode.INVALID_PARAM.value, request=req)

        dense_vector = data_utils.convert_dict(request.dense_vector)
        sparse_vector = data_utils.convert_dict(request.sparse_vector)
        filters = data_utils.convert_dict(request.filter)
        output_fields = data_utils.convert_dict(request.output_fields)
        limit = request.limit or 10
        if limit <= 0:
            return error_response(
                "limit must be greater than 0", ErrorCode.INVALID_PARAM.value, request=req
            )
        offset = request.offset or 0
        if offset < 0:
            return error_response(
                "offset must be greater than or equal to 0",
                ErrorCode.INVALID_PARAM.value,
                request=req,
            )

        result = collection.search_by_vector(
            index_name=index_name,
            dense_vector=dense_vector,
            limit=limit,
            offset=offset,
            filters=filters,
            sparse_vector=sparse_vector,
            output_fields=output_fields,
        )
        return success_response("search success", asdict(result), request=req)
    except VikingDBException as e:
        return error_response(e.message, e.code.value, request=req)


@search_router.post("/id", response_model=ApiResponse)
async def search_by_id(request: SearchByIdRequest, req: Request):
    """Search by ID"""
    try:
        collection = get_collection_or_raise(request.collection_name, request.project or "default")

        index_name = request.index_name
        if not index_name:
            return error_response("index name is empty", ErrorCode.INVALID_PARAM.value, request=req)

        id_value = request.id
        if id_value is None:
            return error_response("id is empty", ErrorCode.INVALID_PARAM.value, request=req)

        filters = data_utils.convert_dict(request.filter)
        output_fields = data_utils.convert_dict(request.output_fields)
        limit = request.limit or 10
        if limit <= 0:
            return error_response(
                "limit must be greater than 0", ErrorCode.INVALID_PARAM.value, request=req
            )
        offset = request.offset or 0
        if offset < 0:
            return error_response(
                "offset must be greater than or equal to 0",
                ErrorCode.INVALID_PARAM.value,
                request=req,
            )

        result = collection.search_by_id(
            index_name=index_name,
            id=id_value,
            limit=limit,
            offset=offset,
            filters=filters,
            output_fields=output_fields,
        )
        return success_response("search success", asdict(result), request=req)
    except VikingDBException as e:
        return error_response(e.message, e.code.value, request=req)


@search_router.post("/multi_modal", response_model=ApiResponse)
async def search_by_multimodal(request: SearchByMultiModalRequest, req: Request):
    """Search by multimodal"""
    try:
        collection = get_collection_or_raise(request.collection_name, request.project or "default")

        index_name = request.index_name
        if not index_name:
            return error_response("index name is empty", ErrorCode.INVALID_PARAM.value, request=req)

        text = request.text
        image = request.image
        video = request.video

        if not text and not image and not video:
            return error_response(
                "at least one of text, image, or video must be provided",
                ErrorCode.INVALID_PARAM.value,
                request=req,
            )

        filters = data_utils.convert_dict(request.filter)
        output_fields = data_utils.convert_dict(request.output_fields)
        limit = request.limit or 10
        if limit <= 0:
            return error_response(
                "limit must be greater than 0", ErrorCode.INVALID_PARAM.value, request=req
            )
        offset = request.offset or 0
        if offset < 0:
            return error_response(
                "offset must be greater than or equal to 0",
                ErrorCode.INVALID_PARAM.value,
                request=req,
            )

        try:
            result = collection.search_by_multimodal(
                index_name=index_name,
                text=text,
                image=image,
                video=video,
                limit=limit,
                offset=offset,
                filters=filters,
                output_fields=output_fields,
            )
            return success_response("search success", asdict(result), request=req)
        except Exception as e:
            logger.error(f"Multimodal search error: {e}")
            return error_response(str(e), ErrorCode.INTERNAL_ERR.value, request=req)
    except VikingDBException as e:
        return error_response(e.message, e.code.value, request=req)


@search_router.post("/scalar", response_model=ApiResponse)
async def search_by_scalar(request: SearchByScalarRequest, req: Request):
    """Search by scalar field"""
    try:
        collection = get_collection_or_raise(request.collection_name, request.project or "default")

        index_name = request.index_name
        if not index_name:
            return error_response("index name is empty", ErrorCode.INVALID_PARAM.value, request=req)

        field = request.field
        if not field:
            return error_response("field is empty", ErrorCode.INVALID_PARAM.value, request=req)

        order = request.order or "desc"
        filters = data_utils.convert_dict(request.filter)
        output_fields = data_utils.convert_dict(request.output_fields)
        limit = request.limit or 10
        if limit <= 0:
            return error_response(
                "limit must be greater than 0", ErrorCode.INVALID_PARAM.value, request=req
            )
        offset = request.offset or 0
        if offset < 0:
            return error_response(
                "offset must be greater than or equal to 0",
                ErrorCode.INVALID_PARAM.value,
                request=req,
            )

        result = collection.search_by_scalar(
            index_name=index_name,
            field=field,
            order=order,
            limit=limit,
            offset=offset,
            filters=filters,
            output_fields=output_fields,
        )
        return success_response("search success", asdict(result), request=req)
    except VikingDBException as e:
        return error_response(e.message, e.code.value, request=req)


@search_router.post("/random", response_model=ApiResponse)
async def search_by_random(request: SearchByRandomRequest, req: Request):
    """Search by random"""
    try:
        collection = get_collection_or_raise(request.collection_name, request.project or "default")

        index_name = request.index_name
        if not index_name:
            return error_response("index name is empty", ErrorCode.INVALID_PARAM.value, request=req)

        filters = data_utils.convert_dict(request.filter)
        output_fields = data_utils.convert_dict(request.output_fields)
        limit = request.limit or 10
        if limit <= 0:
            return error_response(
                "limit must be greater than 0", ErrorCode.INVALID_PARAM.value, request=req
            )
        offset = request.offset or 0
        if offset < 0:
            return error_response(
                "offset must be greater than or equal to 0",
                ErrorCode.INVALID_PARAM.value,
                request=req,
            )

        result = collection.search_by_random(
            index_name=index_name,
            limit=limit,
            offset=offset,
            filters=filters,
            output_fields=output_fields,
        )
        return success_response("search success", asdict(result), request=req)
    except VikingDBException as e:
        return error_response(e.message, e.code.value, request=req)


@search_router.post("/keywords", response_model=ApiResponse)
async def search_by_keywords(request: SearchByKeywordsRequest, req: Request):
    """Search by keywords"""
    try:
        collection = get_collection_or_raise(request.collection_name, request.project or "default")

        index_name = request.index_name
        if not index_name:
            return error_response("index name is empty", ErrorCode.INVALID_PARAM.value, request=req)

        keywords = data_utils.convert_dict(request.keywords)
        query = request.query

        if not keywords and not query:
            return error_response(
                "at least one of keywords or query must be provided",
                ErrorCode.INVALID_PARAM.value,
                request=req,
            )

        filters = data_utils.convert_dict(request.filter)
        output_fields = data_utils.convert_dict(request.output_fields)
        limit = request.limit or 10
        if limit <= 0:
            return error_response(
                "limit must be greater than 0", ErrorCode.INVALID_PARAM.value, request=req
            )
        offset = request.offset or 0
        if offset < 0:
            return error_response(
                "offset must be greater than or equal to 0",
                ErrorCode.INVALID_PARAM.value,
                request=req,
            )

        try:
            result = collection.search_by_keywords(
                index_name=index_name,
                keywords=keywords,
                query=query,
                limit=limit,
                offset=offset,
                filters=filters,
                output_fields=output_fields,
            )
            return success_response("search success", asdict(result), request=req)
        except Exception as e:
            logger.error(f"Keywords search error: {e}")
            return error_response(str(e), ErrorCode.INTERNAL_ERR.value, request=req)
    except VikingDBException as e:
        return error_response(e.message, e.code.value, request=req)


# ==================== Cleanup ====================


def clear_resource():
    """Clean up resources"""
    logger.info("Closing ProjectGroup...")
    project_group.close()
    logger.info("ProjectGroup closed")
