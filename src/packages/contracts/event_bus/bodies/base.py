"""body 베이스 + 직렬화.

to_body(): 객체 → wire dict(발행할 때).
from_body(): wire dict → 객체(기본 strict, 미지 필드 거부).
strict=False: wire consumer에서 미래 additive 필드를 무시.
필드 이름이 곧 wire 키. 카멜케이스는 field(metadata={"payload_name": ...}) 별칭.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import MISSING, dataclass, fields
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

JsonObject = dict[str, Any]


class EventBodyDecodeError(ValueError):
    """payload 가 등록된 body 계약과 불일치할 때 발생"""


@dataclass(frozen=True)
class EventBody:
    def to_body(self) -> JsonObject:
        data: JsonObject = {}
        for item in fields(self):
            key = item.metadata.get("payload_name", item.name)
            data[key] = _to_body_value(getattr(self, item.name))
        return data

    @classmethod
    def from_body(cls, raw: Mapping[str, Any], *, strict: bool = True) -> EventBody:
        # 중첩 body(예: rendered_manifest: RenderedManifest)는 dict 가
        # 아니라 그 타입 객체로 복원 → 워커가 evt.x.y 로 접근.
        if not isinstance(raw, Mapping):
            raise EventBodyDecodeError(f"{cls.__name__}: payload must be an object")
        hints = get_type_hints(cls)
        values: JsonObject = {}
        if strict:
            expected_keys = {item.metadata.get("payload_name", item.name) for item in fields(cls)}
            extra_keys = set(raw) - expected_keys
            if extra_keys:
                names = ", ".join(sorted(str(key) for key in extra_keys))
                raise EventBodyDecodeError(f"{cls.__name__}: unexpected field(s): {names}")
        for item in fields(cls):
            key = item.metadata.get("payload_name", item.name)
            if key not in raw:
                if item.default is not MISSING:
                    values[item.name] = item.default
                    continue
                if item.default_factory is not MISSING:  # type: ignore[attr-defined]
                    values[item.name] = item.default_factory()  # type: ignore[misc]
                    continue
                raise EventBodyDecodeError(f"{cls.__name__}: missing required field: {key}")
            value = raw[key]
            field_type = hints.get(item.name)
            values[item.name] = _decode_value(cls.__name__, key, value, field_type, strict=strict)
        return cls(**values)


def _to_body_value(value: Any) -> Any:
    if isinstance(value, EventBody):
        return value.to_body()
    if isinstance(value, Mapping):
        return {key: _to_body_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_to_body_value(item) for item in value]
    return value


def _decode_value(owner: str, key: str, value: Any, field_type: Any, *, strict: bool) -> Any:
    if field_type is None or field_type is Any:
        return value

    origin = get_origin(field_type)
    args = get_args(field_type)
    if origin in (Union, UnionType):
        if value is None and type(None) in args:
            return None
        for option in args:
            if option is type(None):
                continue
            try:
                return _decode_value(owner, key, value, option, strict=strict)
            except EventBodyDecodeError:
                continue
        raise EventBodyDecodeError(f"{owner}: invalid type for field: {key}")

    if isinstance(field_type, type) and issubclass(field_type, EventBody):
        if not isinstance(value, Mapping):
            raise EventBodyDecodeError(f"{owner}: field {key} must be an object")
        return field_type.from_body(value, strict=strict)

    if origin in (list, Sequence):
        if not isinstance(value, Sequence) or isinstance(value, str | bytes):
            raise EventBodyDecodeError(f"{owner}: field {key} must be a list")
        item_type = args[0] if args else Any
        return [_decode_value(owner, f"{key}[]", item, item_type, strict=strict) for item in value]

    if origin in (dict, Mapping):
        if not isinstance(value, Mapping):
            raise EventBodyDecodeError(f"{owner}: field {key} must be an object")
        return dict(value)

    if field_type is bool:
        if type(value) is not bool:
            raise EventBodyDecodeError(f"{owner}: field {key} must be bool")
        return value
    if field_type is int:
        if type(value) is not int:
            raise EventBodyDecodeError(f"{owner}: field {key} must be int")
        return value
    if field_type is str:
        if not isinstance(value, str):
            raise EventBodyDecodeError(f"{owner}: field {key} must be str")
        return value

    return value
