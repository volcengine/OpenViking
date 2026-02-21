# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from enum import Enum
from typing import Annotated, Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from openviking.storage.vectordb.utils.id_generator import generate_auto_id


# Custom ValidationError for compatibility
class ValidationError(Exception):
    def __init__(self, message: str, field_path: str = None):
        self.field_path = field_path
        super().__init__(message)


# --- Basic Validators ---


def validate_name_str(name: str) -> str:
    if not name:
        raise ValueError("name is empty or None")
    if not (1 <= len(name) <= 128):
        raise ValueError(f"name length must be between 1 and 128, got {len(name)}")
    if not name[0].isalpha():
        raise ValueError(f"name must start with a letter, got '{name[0]}'")
    invalid_chars = [c for c in name if not (c.isalnum() or c == "_")]
    if invalid_chars:
        raise ValueError(
            f"name can only contain letters, numbers and underscore, found invalid characters: {invalid_chars}"
        )
    return name


ValidName = Annotated[
    str,
    Field(min_length=1, max_length=128),
    field_validator("name", mode="before", check_fields=False)(validate_name_str),
]

# --- Models ---


class FieldTypeEnum(str, Enum):
    INT64 = "int64"
    FLOAT32 = "float32"
    STRING = "string"
    BOOL = "bool"
    LIST_STRING = "list<string>"
    LIST_INT64 = "list<int64>"
    VECTOR = "vector"
    SPARSE_VECTOR = "sparse_vector"
    TEXT = "text"
    PATH = "path"
    IMAGE = "image"
    VIDEO = "video"
    DATE_TIME = "date_time"
    GEO_POINT = "geo_point"


class DenseVectorize(BaseModel):
    model_config = ConfigDict()

    ModelName: str
    ModelVersion: Optional[str] = None
    TextField: Optional[str] = None
    ImageField: Optional[str] = None
    VideoField: Optional[str] = None
    Dim: Optional[int] = None
    Dimension: Optional[int] = None

    @field_validator("TextField", "ImageField", "VideoField")
    @classmethod
    def check_fields(cls, v):
        # We enforce presence logic in model_validator if needed,
        # but the original code had strict checks inside validate_dense_vectorize
        # Original: "TextField" is required
        return v

    @model_validator(mode="after")
    def check_required(self):
        if self.TextField is None and self.ImageField is None and self.VideoField is None:
            # Original logic: if "text_field" not in vectorize["dense"]
            # The old code strictly required "TextField" (or "text_field" in logic, but schema used "TextField")
            # Actually, old code: if "text_field" not in vectorize["dense"]: return False
            # But ALLOWED keys used "TextField". It seems there's a case sensitivity issue in old code or intended.
            # The old code check keys: 'ModelName', 'TextField' against ALLOWED_COLLECTION_DENSE_VECTORIZE_CHECK
            # Let's assume PascalCase as per ALLOWED dictionary keys.
            if not self.TextField:
                raise ValueError("vectorize dense must contain TextField")
        return self


class SparseVectorize(BaseModel):
    model_config = ConfigDict()

    ModelName: str
    ModelVersion: Optional[str] = None
    TextField: Optional[str] = None


class VectorizeConfig(BaseModel):
    model_config = ConfigDict()

    Dense: Optional[DenseVectorize] = None
    Sparse: Optional[SparseVectorize] = None


class CollectionField(BaseModel):
    model_config = ConfigDict()

    FieldName: str
    FieldType: FieldTypeEnum
    Dim: Optional[int] = Field(None, ge=4, le=4096)
    IsPrimaryKey: Optional[bool] = False
    DefaultValue: Optional[Any] = None

    # Internal fields
    _FieldID: Optional[int] = None

    @field_validator("FieldName")
    @classmethod
    def validate_fieldname(cls, v):
        return validate_name_str(v)

    @field_validator("Dim")
    @classmethod
    def validate_dim(cls, v):
        if v is not None:
            if v % 4 != 0:
                raise ValueError(f"dimension must be a multiple of 4, got {v}")
        return v

    @model_validator(mode="after")
    def validate_field_logic(cls, m):
        if m.FieldType == FieldTypeEnum.VECTOR:
            if m.Dim is None:
                raise ValueError("vector field must contain dim")
        if m.IsPrimaryKey:
            if m.FieldType not in (FieldTypeEnum.INT64, FieldTypeEnum.STRING):
                raise ValueError(f"primary key must be int64 or string, got '{m.FieldType}'")
        return m


class CollectionMetaConfig(BaseModel):
    model_config = ConfigDict()

    CollectionName: str
    Fields: List[CollectionField]
    ProjectName: Optional[str] = None
    Description: Optional[str] = Field(None, max_length=65535)
    Vectorize: Optional[VectorizeConfig] = None

    # Internal fields
    _FieldsCount: Optional[int] = None

    @field_validator("CollectionName", "ProjectName")
    @classmethod
    def validate_names(cls, v):
        if v is None:
            return v
        return validate_name_str(v)

    @field_validator("Fields")
    @classmethod
    def validate_fields_list(cls, fields):
        names = set()
        has_pk = False
        for f in fields:
            if f.FieldName in names:
                raise ValueError(f"duplicate field name '{f.FieldName}'")
            names.add(f.FieldName)
            if f.IsPrimaryKey:
                if has_pk:
                    raise ValueError("multiple primary keys are not allowed")
                has_pk = True
        return fields


class VectorIndexConfig(BaseModel):
    model_config = ConfigDict()

    IndexType: Literal["flat", "flat_hybrid", "FLAT", "FLAT_HYBRID"]
    Distance: Optional[Literal["l2", "ip", "cosine", "L2", "IP", "COSINE"]] = None
    Quant: Optional[Literal["int8", "float", "fix16", "pq", "INT8", "FLOAT", "FIX16", "PQ"]] = None
    DiskannM: Optional[int] = None
    DiskannCef: Optional[int] = None
    PqCodeRatio: Optional[float] = None
    CacheRatio: Optional[float] = None
    SearchWithSparseLogitAlpha: Optional[float] = None
    IndexWithSparseLogitAlpha: Optional[float] = None
    EnableSparse: Optional[bool] = None

    @field_validator("IndexType", "Distance", "Quant", mode="before")
    @classmethod
    def case_insensitive(cls, v):
        if isinstance(v, str):
            # Normalize to lowercase for checking, but model definition uses mixed case literals?
            # Actually user input might be mixed. Pydantic validates against Literals.
            # Let's normalize everything to lower case if the Literal allows it, or just pass through.
            # The old code did .lower() checks.
            pass
        return v

    @field_validator("IndexType")
    @classmethod
    def validate_index_type(cls, v):
        if v.lower() not in ["flat", "flat_hybrid"]:
            raise ValueError(f"invalid index type '{v}'")
        return v

    @field_validator("Distance")
    @classmethod
    def validate_distance(cls, v):
        if v and v.lower() not in ["l2", "ip", "cosine"]:
            raise ValueError(f"invalid distance type '{v}'")
        return v

    @field_validator("Quant")
    @classmethod
    def validate_quant(cls, v):
        if v and v.lower() not in ["int8", "float", "fix16", "pq"]:
            raise ValueError(f"invalid quant type '{v}'")
        return v


class IndexMetaConfig(BaseModel):
    model_config = ConfigDict()

    IndexName: str
    VectorIndex: VectorIndexConfig
    ScalarIndex: Optional[List[str]] = None
    Description: Optional[str] = Field(None, max_length=65535)
    ProjectName: Optional[str] = None
    CollectionName: Optional[str] = None

    @field_validator("IndexName", "ProjectName", "CollectionName")
    @classmethod
    def validate_names(cls, v):
        if v is None:
            return v
        return validate_name_str(v)


class IndexMetaUpdateConfig(BaseModel):
    model_config = ConfigDict()

    IndexName: Optional[str] = None
    VectorIndex: Optional[VectorIndexConfig] = None
    ScalarIndex: Optional[List[str]] = None
    Description: Optional[str] = Field(None, max_length=65535)
    ProjectName: Optional[str] = None
    CollectionName: Optional[str] = None

    @field_validator("IndexName", "ProjectName", "CollectionName")
    @classmethod
    def validate_names(cls, v):
        if v is None:
            return v
        return validate_name_str(v)


class CollectionMetaUpdateConfig(BaseModel):
    model_config = ConfigDict()

    CollectionName: Optional[str] = None
    Fields: Optional[List[CollectionField]] = None
    ProjectName: Optional[str] = None
    Description: Optional[str] = Field(None, max_length=65535)
    Vectorize: Optional[VectorizeConfig] = None

    @field_validator("CollectionName", "ProjectName")
    @classmethod
    def validate_names(cls, v):
        if v is None:
            return v
        return validate_name_str(v)

    @field_validator("Fields")
    @classmethod
    def validate_fields_list(cls, fields):
        if fields is None:
            return fields
        names = set()
        has_pk = False
        for f in fields:
            if f.FieldName in names:
                raise ValueError(f"duplicate field name '{f.FieldName}'")
            names.add(f.FieldName)
            if f.IsPrimaryKey:
                if has_pk:
                    raise ValueError("multiple primary keys are not allowed")
                has_pk = True
        return fields


# --- Helper / Compatibility Functions ---


def _handle_validation_error(e: PydanticValidationError):
    # Convert Pydantic ValidationError to our custom ValidationError string format
    # to maintain some resemblance of old error messages if needed,
    # or just raise our custom exception.
    msg = str(e)
    # Extract first error for cleaner message
    try:
        err = e.errors()[0]
        field = ".".join(str(x) for x in err["loc"])
        msg = f"{err['msg']} (field: {field})"
    except:
        pass
    raise ValidationError(msg)


def validate_collection_meta_data(meta_data: dict) -> None:
    try:
        CollectionMetaConfig.model_validate(meta_data)
    except PydanticValidationError as e:
        _handle_validation_error(e)


def is_valid_collection_meta_data(meta_data: dict) -> bool:
    try:
        validate_collection_meta_data(meta_data)
        return True
    except ValidationError:
        return False


def validate_collection_meta_data_for_update(meta_data: dict, field_meta_dict: dict = None) -> None:
    try:
        CollectionMetaUpdateConfig.model_validate(meta_data)
    except PydanticValidationError as e:
        _handle_validation_error(e)


def is_valid_collection_meta_data_for_update(meta_data: dict, field_meta_dict: dict = None) -> bool:
    try:
        validate_collection_meta_data_for_update(meta_data, field_meta_dict)
        return True
    except ValidationError:
        return False


def validate_index_meta_data(meta_data: dict, field_meta_dict: dict) -> None:
    try:
        model = IndexMetaConfig.model_validate(meta_data)
        # Extra logic for ScalarIndex validation against field_meta_dict
        if model.ScalarIndex:
            unknown_fields = set(model.ScalarIndex) - set(field_meta_dict.keys())
            if unknown_fields:
                raise ValidationError(
                    f"scalar index contains unknown fields: {list(unknown_fields)}"
                )
    except PydanticValidationError as e:
        _handle_validation_error(e)


def is_valid_index_meta_data(meta_data: dict, field_meta_dict: dict) -> bool:
    try:
        validate_index_meta_data(meta_data, field_meta_dict)
        return True
    except ValidationError:
        return False


def validate_index_meta_data_for_update(meta_data: dict, field_meta_dict: dict) -> None:
    try:
        model = IndexMetaUpdateConfig.model_validate(meta_data)
        if model.ScalarIndex:
            unknown_fields = set(model.ScalarIndex) - set(field_meta_dict.keys())
            if unknown_fields:
                raise ValidationError(
                    f"scalar index contains unknown fields: {list(unknown_fields)}"
                )
    except PydanticValidationError as e:
        _handle_validation_error(e)


def is_valid_index_meta_data_for_update(meta_data: dict, field_meta_dict: dict) -> bool:
    try:
        validate_index_meta_data_for_update(meta_data, field_meta_dict)
        return True
    except ValidationError:
        return False


def fix_collection_meta(meta_data: dict) -> dict:
    fields = meta_data.get("Fields", [])
    has_pk = False
    for item in fields:
        if item.get("IsPrimaryKey", False):
            has_pk = True
            break

    if not has_pk:
        fields.append(
            {
                "FieldName": "AUTO_ID",
                "FieldType": "int64",
                "IsPrimaryKey": True,
            }
        )

    field_count = meta_data.get("_FieldsCount", 0)
    for item in fields:
        if "_FieldID" not in item:
            item["_FieldID"] = field_count
            field_count += 1

    meta_data["_FieldsCount"] = field_count
    meta_data["Fields"] = fields
    return meta_data


# Data validation logic
REQUIRED_COLLECTION_FIELD_TYPE_CHECK = {
    "int64": ([int], None, 0),
    "float32": ([int, float], None, 0.0),
    "string": ([str], lambda l: len(l) <= 1024, "default"),
    "bool": ([bool], None, False),
    "list<string>": (
        [list],
        lambda l: all(isinstance(item, str) for item in l),
        ["default"],
    ),
    "list<int64>": ([list], lambda l: all(isinstance(item, int) for item in l), [0]),
    "vector": (
        [list],
        # dim check is done elsewhere or we assume valid if it's a list of floats
        lambda l: all(isinstance(item, (int, float)) for item in l),
        [],
    ),
    "text": ([str], None, ""),
    "path": ([str], None, ""),
    "image": ([str], None, ""),
    "video": ([dict], None, {}),
    "date_time": ([str], None, ""),
    "geo_point": ([str], None, ""),
    "sparse_vector": ([dict], None, {}),
}


def validate_fields_data(field_data_dict: dict, field_meta_dict: dict) -> None:
    if len(field_data_dict) > len(field_meta_dict):
        raise ValidationError(
            f"too many fields: got {len(field_data_dict)}, expected max {len(field_meta_dict)}"
        )

    for field_name, field_value in field_data_dict.items():
        if field_name not in field_meta_dict:
            raise ValidationError(f"unknown field '{field_name}'")

        field_type = field_meta_dict[field_name]["FieldType"]
        # Compatibility with enum if using Pydantic model for meta dict
        if hasattr(field_type, "value"):
            field_type = field_type.value

        if field_type not in REQUIRED_COLLECTION_FIELD_TYPE_CHECK:
            # Should be caught by meta validation, but safety check
            continue

        allowed_types, validator, _ = REQUIRED_COLLECTION_FIELD_TYPE_CHECK[field_type]

        if type(field_value) not in allowed_types:
            raise ValidationError(
                f"field type mismatch for '{field_name}': expected {field_type}, got {type(field_value).__name__}"
            )

        if validator and not validator(field_value):
            raise ValidationError(f"invalid value for field '{field_name}'")


def is_valid_fields_data(field_data_dict: dict, field_meta_dict: dict) -> bool:
    try:
        validate_fields_data(field_data_dict, field_meta_dict)
        return True
    except ValidationError:
        # print(f"ValidationError {e}") # Reduce noise
        return False


def fix_fields_data(field_data_dict: dict, field_meta_dict: dict) -> dict:
    if len(field_data_dict) >= len(field_meta_dict):
        return field_data_dict

    for field_name, field_meta in field_meta_dict.items():
        if field_name not in field_data_dict:
            # Handle both dict access and object access if field_meta is a Model (though here it's likely a dict)
            if isinstance(field_meta, dict):
                field_type = field_meta["FieldType"]
                default_val = field_meta.get("DefaultValue")
            else:
                field_type = field_meta.FieldType
                default_val = field_meta.DefaultValue

            if hasattr(field_type, "value"):
                field_type = field_type.value

            if default_val is not None:
                field_data_dict[field_name] = default_val
            elif field_name == "AUTO_ID":
                field_data_dict[field_name] = generate_auto_id()
            else:
                field_data_dict[field_name] = REQUIRED_COLLECTION_FIELD_TYPE_CHECK[field_type][2]
    return field_data_dict
