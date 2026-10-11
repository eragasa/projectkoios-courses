"""Mypy-enforced class taxonomy for Courses reference resolution."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from mypy.nodes import (
    ARG_NAMED,
    CallExpr,
    ClassDef,
    Decorator,
    Expression,
    ExpressionStmt,
    FuncDef,
    Import,
    ImportAll,
    ImportFrom,
    IndexExpr,
    MemberExpr,
    MypyFile,
    NameExpr,
    PassStmt,
    RefExpr,
    StrExpr,
)
from mypy.plugin import ClassDefContext, Plugin

_DIAGNOSTIC: Final = "[courses-reference-taxonomy]"
_PRODUCTION_MODULE: Final = "projectkoios.courses.reference_resolution"
_FIXTURE_MODULE_PREFIX: Final = "courses_reference_resolution_fixture_"
_DATA_OBJECT_BASE: Final = (
    "projectkoios.courses.reference_resolution_taxonomy."
    "CourseReferenceDataObject"
)
_REQUEST_BASE: Final = (
    "projectkoios.courses.reference_resolution_taxonomy."
    "CourseReferenceActionRequest"
)
_RESULT_BASE: Final = (
    "projectkoios.courses.reference_resolution_taxonomy."
    "CourseReferenceActionResult"
)
_ACTIONIZER_BASE: Final = (
    "projectkoios.courses.reference_resolution_taxonomy."
    "CourseReferenceActionizer"
)
_DATA_ROLES: Final = frozenset({_DATA_OBJECT_BASE, _REQUEST_BASE, _RESULT_BASE})
_ERROR_BASES: Final = frozenset(
    {
        "builtins.BaseException",
        "builtins.Exception",
        "builtins.RuntimeError",
        "builtins.TypeError",
        "builtins.ValueError",
    }
)
_ENUM_BASES: Final = frozenset(
    {
        "enum.Enum",
        "enum.IntEnum",
        "enum.IntFlag",
        "enum.Flag",
        "enum.ReprEnum",
        "enum.StrEnum",
    }
)
_PROTOCOL_BASES: Final = frozenset(
    {"typing.Protocol", "typing_extensions.Protocol"}
)


class CourseReferenceResolutionTaxonomyPlugin(Plugin):
    """Reject nonconforming classes in the bounded Courses capability."""

    def get_customize_class_mro_hook(
        self,
        fullname: str,
    ) -> Callable[[ClassDefContext], None] | None:
        """Return the taxonomy callback for every bounded class."""
        if _is_target(fullname):
            return self._check_class
        return None

    def _check_class(self, context: ClassDefContext) -> None:
        module_name = _module_name(context.cls.fullname)
        module = (
            self._modules.get(module_name)
            if self._modules is not None
            else None
        )
        if module is not None:
            _check_private_imports(context, module)
        _check_class_shape(context, module_name)


def plugin(version: str) -> type[CourseReferenceResolutionTaxonomyPlugin]:
    """Return the repository-local mypy plugin type."""
    del version
    return CourseReferenceResolutionTaxonomyPlugin


def _is_target(fullname: str) -> bool:
    return (
        fullname == _PRODUCTION_MODULE
        or fullname.startswith(f"{_PRODUCTION_MODULE}.")
        or fullname.startswith(_FIXTURE_MODULE_PREFIX)
    )


def _module_name(fullname: str) -> str:
    if fullname == _PRODUCTION_MODULE or fullname.startswith(
        f"{_PRODUCTION_MODULE}."
    ):
        return _PRODUCTION_MODULE
    if fullname.startswith(_FIXTURE_MODULE_PREFIX):
        return fullname.split(".", maxsplit=1)[0]
    return fullname.rpartition(".")[0]


def _check_private_imports(
    context: ClassDefContext,
    module: MypyFile,
) -> None:
    first_class = next(
        (
            statement
            for statement in module.defs
            if isinstance(statement, ClassDef)
        ),
        None,
    )
    if first_class is not context.cls:
        return
    for imported in module.imports:
        if isinstance(imported, ImportFrom):
            if imported.id != "__future__" and (
                _has_private_segment(imported.id)
                or any(name.startswith("_") for name, _ in imported.names)
            ):
                context.api.fail(
                    f"{_DIAGNOSTIC} private cross-module import is forbidden",
                    imported,
                )
        elif isinstance(imported, (Import, ImportAll)):
            imported_ids = (
                [name for name, _ in imported.ids]
                if isinstance(imported, Import)
                else [imported.id]
            )
            if any(_has_private_segment(name) for name in imported_ids):
                context.api.fail(
                    f"{_DIAGNOSTIC} private cross-module import is forbidden",
                    imported,
                )


def _has_private_segment(fullname: str) -> bool:
    return any(
        segment.startswith("_") and segment != "__future__"
        for segment in fullname.split(".")
    )


def _check_class_shape(
    context: ClassDefContext,
    module_name: str,
) -> None:
    definition = context.cls
    if definition.name.startswith("_"):
        context.api.fail(
            f"{_DIAGNOSTIC} private classes are forbidden",
            definition,
        )
    relative_name = definition.fullname.removeprefix(f"{module_name}.")
    if "." in relative_name:
        context.api.fail(
            f"{_DIAGNOSTIC} nested classes are forbidden",
            definition,
        )
    bases = frozenset(
        fullname
        for expression in (
            definition.base_type_exprs + definition.removed_base_type_exprs
        )
        if (fullname := _base_fullname(expression)) is not None
    )
    role = "protocol" if definition.info.is_protocol else _class_role(bases)
    if role is None:
        context.api.fail(
            f"{_DIAGNOSTIC} class lacks an allowed nominal taxonomy role",
            definition,
        )
        return
    _check_initializer(context, definition)
    _check_private_methods(context, definition)
    if role == "data":
        _check_data_object(context, definition)
    elif role == "actionizer":
        _check_actionizer(context, definition)


def _base_fullname(expression: Expression) -> str | None:
    while isinstance(expression, IndexExpr):
        expression = expression.base
    if isinstance(expression, RefExpr):
        return expression.fullname
    if isinstance(expression, MemberExpr):
        return expression.fullname
    return None


def _class_role(bases: frozenset[str]) -> str | None:
    if bases & _DATA_ROLES:
        return "data"
    if _ACTIONIZER_BASE in bases:
        return "actionizer"
    if bases & _ENUM_BASES:
        return "enum"
    if bases & _ERROR_BASES:
        return "error"
    if bases & _PROTOCOL_BASES:
        return "protocol"
    return None


def _methods(definition: ClassDef) -> tuple[FuncDef, ...]:
    values: list[FuncDef] = []
    for statement in definition.defs.body:
        if isinstance(statement, FuncDef):
            values.append(statement)
        elif isinstance(statement, Decorator):
            values.append(statement.func)
    return tuple(values)


def _check_initializer(
    context: ClassDefContext,
    definition: ClassDef,
) -> None:
    for method in _methods(definition):
        if method.name != "__init__":
            continue
        statements = method.body.body
        if len(statements) != 1 or not isinstance(
            statements[0], (ExpressionStmt, PassStmt)
        ):
            context.api.fail(
                f"{_DIAGNOSTIC} initializers must be docstring-only",
                method,
            )
            continue
        if isinstance(statements[0], ExpressionStmt) and not isinstance(
            statements[0].expr, StrExpr
        ):
            context.api.fail(
                f"{_DIAGNOSTIC} initializers must be docstring-only",
                method,
            )


def _check_private_methods(
    context: ClassDefContext,
    definition: ClassDef,
) -> None:
    for method in _methods(definition):
        if method.name.startswith("_") and not (
            method.name.startswith("__") and method.name.endswith("__")
        ):
            context.api.fail(
                f"{_DIAGNOSTIC} private helpers must be top-level functions",
                method,
            )


def _check_data_object(
    context: ClassDefContext,
    definition: ClassDef,
) -> None:
    dataclass = next(
        (
            decorator
            for decorator in definition.decorators
            if _is_dataclass_decorator(decorator)
        ),
        None,
    )
    if dataclass is None:
        context.api.fail(
            f"{_DIAGNOSTIC} data objects must use @dataclass",
            definition,
        )
    elif not (
        _decorator_true_keyword(dataclass, "frozen")
        and _decorator_true_keyword(dataclass, "slots")
    ):
        context.api.fail(
            f"{_DIAGNOSTIC} data objects must be frozen and slotted",
            definition,
        )
    for method in _methods(definition):
        if method.name not in {"__post_init__"}:
            context.api.fail(
                f"{_DIAGNOSTIC} data objects must be passive",
                method,
            )


def _check_actionizer(
    context: ClassDefContext,
    definition: ClassDef,
) -> None:
    public_methods = [
        method.name
        for method in _methods(definition)
        if not method.name.startswith("_")
    ]
    if public_methods != ["action"]:
        context.api.fail(
            f"{_DIAGNOSTIC} actionizers must expose exactly one action method",
            definition,
        )
    if definition.name.endswith("Factory"):
        context.api.fail(
            f"{_DIAGNOSTIC} factories are forbidden",
            definition,
        )


def _is_dataclass_decorator(expression: Expression) -> bool:
    callee = (
        expression.callee if isinstance(expression, CallExpr) else expression
    )
    if isinstance(callee, NameExpr):
        return callee.fullname == "dataclasses.dataclass" or (
            callee.fullname is None and callee.name == "dataclass"
        )
    if isinstance(callee, MemberExpr):
        return callee.fullname == "dataclasses.dataclass"
    return False


def _decorator_true_keyword(expression: Expression, keyword: str) -> bool:
    if not isinstance(expression, CallExpr):
        return False
    for name, kind, argument in zip(
        expression.arg_names,
        expression.arg_kinds,
        expression.args,
        strict=True,
    ):
        if (
            name == keyword
            and kind == ARG_NAMED
            and isinstance(argument, NameExpr)
            and argument.name == "True"
        ):
            return True
    return False
