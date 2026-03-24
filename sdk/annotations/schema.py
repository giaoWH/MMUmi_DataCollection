from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sdk.config import load_config_file

VALID_ANNOTATION_SCOPES = ("session", "span", "keyframe")
VALID_ANNOTATION_TYPES = ("string", "bool", "number", "enum", "multi_enum")
ANNOTATION_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class AnnotationField:
    id: str
    scope: str
    type: str
    label: str
    required: bool = False
    default: Any = None
    options: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "type": self.type,
            "label": self.label,
            "required": self.required,
            "default": self.default,
            "options": list(self.options),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AnnotationField":
        field = cls(
            id=str(payload["id"]),
            scope=str(payload["scope"]),
            type=str(payload["type"]),
            label=str(payload["label"]),
            required=bool(payload.get("required", False)),
            default=payload.get("default"),
            options=[str(item) for item in payload.get("options", [])],
        )
        field.validate()
        return field

    def validate(self) -> None:
        if not self.id.strip():
            raise ValueError("annotation field id 不能为空")
        if self.scope not in VALID_ANNOTATION_SCOPES:
            raise ValueError(f"annotation field {self.id} scope 非法: {self.scope}")
        if self.type not in VALID_ANNOTATION_TYPES:
            raise ValueError(f"annotation field {self.id} type 非法: {self.type}")
        if not self.label.strip():
            raise ValueError(f"annotation field {self.id} label 不能为空")

        if self.type in {"enum", "multi_enum"}:
            if not self.options:
                raise ValueError(f"annotation field {self.id} 需要提供 options")
            if len(set(self.options)) != len(self.options):
                raise ValueError(f"annotation field {self.id} options 不能重复")
        elif self.options:
            raise ValueError(f"annotation field {self.id} 只有 enum / multi_enum 可以提供 options")

        self._validate_value(self.default, allow_none=True)

    def _validate_value(self, value: Any, *, allow_none: bool) -> None:
        if value is None:
            if allow_none:
                return
            raise ValueError(f"annotation field {self.id} 不能为空")

        if self.type == "string":
            if not isinstance(value, str):
                raise ValueError(f"annotation field {self.id} 需要 string")
            return
        if self.type == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"annotation field {self.id} 需要 bool")
            return
        if self.type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"annotation field {self.id} 需要 number")
            return
        if self.type == "enum":
            if not isinstance(value, str):
                raise ValueError(f"annotation field {self.id} 需要 string")
            if value in self.options:
                return
            if self._supports_custom_other_value() and value.strip():
                return
            raise ValueError(f"annotation field {self.id} 默认值必须在 options 中")
            return
        if self.type == "multi_enum":
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError(f"annotation field {self.id} 需要 string 列表")
            invalid = [item for item in value if not self._is_allowed_multi_enum_item(item)]
            if invalid:
                raise ValueError(f"annotation field {self.id} 默认值不在 options 中: {invalid}")

    def validate_annotation_value(self, value: Any) -> None:
        self._validate_value(value, allow_none=not self.required)
        if self.type == "enum" and self._supports_custom_other_value() and value == "other":
            raise ValueError(f"annotation field {self.id} 选择 other 时必须提供自定义标签")
        if self.type == "multi_enum" and self._supports_custom_other_value() and isinstance(value, list):
            if any(item == "other" for item in value):
                raise ValueError(f"annotation field {self.id} 选择 other 时必须提供自定义标签")

    def _supports_custom_other_value(self) -> bool:
        return self.type in {"enum", "multi_enum"} and "other" in self.options

    def _is_allowed_multi_enum_item(self, item: str) -> bool:
        if item in self.options:
            return True
        return self._supports_custom_other_value() and bool(item.strip())


@dataclass(frozen=True)
class AnnotationSchema:
    version: str
    fields: list[AnnotationField]

    def __post_init__(self) -> None:
        field_ids_by_scope: dict[str, set[str]] = {scope: set() for scope in VALID_ANNOTATION_SCOPES}
        for field in self.fields:
            if field.id in field_ids_by_scope[field.scope]:
                raise ValueError(f"annotation field 重复: {field.scope}.{field.id}")
            field_ids_by_scope[field.scope].add(field.id)

    @classmethod
    def default(cls) -> "AnnotationSchema":
        return cls(
            version=ANNOTATION_SCHEMA_VERSION,
            fields=[
                AnnotationField(id="task_name", scope="session", type="string", label="Task Name"),
                AnnotationField(id="instruction", scope="session", type="string", label="Instruction"),
                AnnotationField(id="success", scope="session", type="bool", label="Success"),
                AnnotationField(id="failure_reason", scope="session", type="string", label="Failure Reason"),
                AnnotationField(
                    id="usable_for_training",
                    scope="session",
                    type="bool",
                    label="Usable For Training",
                    default=True,
                ),
                AnnotationField(
                    id="phase",
                    scope="span",
                    type="enum",
                    label="Phase",
                    options=["approach", "contact", "manipulate", "release", "other"],
                ),
                AnnotationField(
                    id="event_tags",
                    scope="span",
                    type="multi_enum",
                    label="Event Tags",
                    default=[],
                    options=["grasp", "place", "align", "insert", "handover", "other"],
                ),
                AnnotationField(
                    id="human_intervention",
                    scope="span",
                    type="bool",
                    label="Human Intervention",
                    default=False,
                ),
                AnnotationField(
                    id="valid_segment",
                    scope="span",
                    type="bool",
                    label="Valid Segment",
                    default=True,
                ),
                AnnotationField(
                    id="event",
                    scope="keyframe",
                    type="enum",
                    label="Event",
                    options=["contact", "grasped", "released", "collision", "failure", "other"],
                ),
                AnnotationField(
                    id="object_state",
                    scope="keyframe",
                    type="enum",
                    label="Object State",
                    options=["unknown", "free", "held", "placed", "misaligned"],
                    default="unknown",
                ),
                AnnotationField(
                    id="quality_tags",
                    scope="keyframe",
                    type="multi_enum",
                    label="Quality Tags",
                    default=[],
                    options=["blur", "occlusion", "sensor_drop", "lighting_issue", "other"],
                ),
            ],
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AnnotationSchema":
        raw_fields = payload.get("fields")
        if raw_fields is None and "scopes" in payload:
            raw_fields = []
            scopes_payload = payload.get("scopes", {})
            if not isinstance(scopes_payload, dict):
                raise ValueError("annotation schema scopes 必须是对象")
            for scope, scope_fields in scopes_payload.items():
                if not isinstance(scope_fields, list):
                    raise ValueError(f"annotation schema scope {scope} 必须是列表")
                for field_payload in scope_fields:
                    merged = dict(field_payload)
                    merged.setdefault("scope", scope)
                    raw_fields.append(merged)
        if not isinstance(raw_fields, list):
            raise ValueError("annotation schema fields 必须是列表")

        return cls(
            version=str(payload.get("version", ANNOTATION_SCHEMA_VERSION)),
            fields=[AnnotationField.from_dict(item) for item in raw_fields],
        )

    @classmethod
    def load(cls, path: str) -> "AnnotationSchema":
        payload = load_config_file(path)
        return cls.from_dict(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "fields": [field.to_dict() for field in self.fields],
        }

    def fields_for_scope(self, scope: str) -> list[AnnotationField]:
        if scope not in VALID_ANNOTATION_SCOPES:
            raise ValueError(f"annotation scope 非法: {scope}")
        return [field for field in self.fields if field.scope == scope]

    def defaults_for_scope(self, scope: str) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        for field in self.fields_for_scope(scope):
            if field.default is not None:
                defaults[field.id] = field.default
        return defaults

    def validate_annotation_data(self, scope: str, payload: dict[str, Any]) -> dict[str, Any]:
        if scope not in VALID_ANNOTATION_SCOPES:
            raise ValueError(f"annotation scope 非法: {scope}")
        if not isinstance(payload, dict):
            raise ValueError(f"{scope} annotation data 必须是对象")

        normalized: dict[str, Any] = {}
        fields = {field.id: field for field in self.fields_for_scope(scope)}
        unknown_fields = sorted(set(payload.keys()) - set(fields.keys()))
        if unknown_fields:
            raise ValueError(f"{scope} annotation 含未知字段: {', '.join(unknown_fields)}")

        for field in fields.values():
            value = payload.get(field.id, field.default)
            if value is None and not field.required:
                continue
            field.validate_annotation_value(value)
            normalized[field.id] = value
        return normalized


def load_annotation_schema(schema_path: str | None = None) -> AnnotationSchema:
    if schema_path:
        return AnnotationSchema.load(schema_path)
    return AnnotationSchema.default()
