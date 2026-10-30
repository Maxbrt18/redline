"""A JSON value read against a shape of `redline.model.notebook_views`.

It knows what the contract declares: TypedDicts, lists, dicts, unions, Literals and scalars.
A missing key, an undeclared key and a wrong type are each a problem, named by their path.
"""

from __future__ import annotations

import types
import typing
from typing import Any


def problems(shape: Any, value: Any, path: str = "$") -> list[str]:
    if shape is Any:
        return []
    if typing.is_typeddict(shape):
        return _object(shape, value, path)
    origin = typing.get_origin(shape)
    if origin in (typing.Union, types.UnionType):
        options = [option for option in typing.get_args(shape)
                   if value is None or option is not type(None)]
        return min((problems(option, value, path) for option in options), key=len)
    if origin is typing.Literal:
        allowed = typing.get_args(shape)
        return [] if value in allowed else [f"{path}: {value!r} is not one of {allowed}"]
    if origin is list:
        if not isinstance(value, list):
            return [f"{path}: expected a list, got {value!r}"]
        [item] = typing.get_args(shape)
        return [found for index, element in enumerate(value)
                for found in problems(item, element, f"{path}[{index}]")]
    if origin is dict:
        if not isinstance(value, dict):
            return [f"{path}: expected an object, got {value!r}"]
        [_, item] = typing.get_args(shape)
        return [found for key, element in value.items()
                for found in problems(item, element, f"{path}.{key}")]
    return [] if _scalar(shape, value) else [f"{path}: expected {shape.__name__}, got {value!r}"]


def _scalar(shape: type, value: Any) -> bool:
    if shape is float:
        return isinstance(value, int | float) and not isinstance(value, bool)
    if shape is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, shape)


def _object(shape: Any, value: Any, path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path}: expected {shape.__name__}, got {value!r}"]
    fields = typing.get_type_hints(shape)
    found = [f"{path}.{key}: missing" for key in sorted(shape.__required_keys__ - set(value))]
    found += [f"{path}.{key}: not in {shape.__name__}" for key in sorted(set(value) - set(fields))]
    for key in fields.keys() & value.keys():
        found += problems(fields[key], value[key], f"{path}.{key}")
    return found
