"""Scenario YAML: models, variable interpolation, and the dry-run validator.

A scenario is data, never code: it names a cell, an action below ``/v1``,
and a JSON body. Action paths and body fields are **not** kept in a
constant table here — they are checked against each cell's live
``GET /openapi.json`` so an added L1 route needs no L2 change (spec §8.1).

The validator (spec §8.2) is a dry run: the only network calls it makes
are ``GET /openapi.json`` and ``GET /v1/health``. It never sends a
state-changing request.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from orchestrator.client import CellCallError, CellClient
from orchestrator.registry import Registry

#: ``${params.volume_uL}`` / ``${measured.weight_g}``
PLACEHOLDER_RE = re.compile(r"\$\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}")
PARAMS_ROOT = "params"
#: Variable root that an ``until:`` expression uses for the latest
#: response of its own polled GET.
UNTIL_RESULT_ROOT = "result"
API_PREFIX = "/v1/"
SNAKE_CASE_RE = r"^[a-z][a-z0-9_]*$"

#: Hops ``_deref`` follows before giving up on a cyclic ``$ref`` chain.
MAX_REF_DEPTH = 5

#: Codes the validator emits. The first six are the M2 acceptance set.
VALIDATION_CODES = (
    "schema",
    "unknown_cell",
    "unknown_action",
    "body_mismatch",
    "var_order",
    "parallel_duplicate_cell",
    "cell_unreachable",
)

_ALLOWED_ASSERT_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.UnaryOp,
    ast.BinOp,
    ast.Compare,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.List,
    ast.Tuple,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.USub,
    ast.UAdd,
    ast.Load,
    ast.Name,  # only the literals below; every other name is rejected
)

#: The only bare names an assert may use. Scenario authors think in YAML
#: and JSON, so ``true``/``false``/``null`` are accepted alongside the
#: Python spellings (which arrive as constants, not names).
ASSERT_NAMES: dict[str, Any] = {
    "true": True,
    "false": False,
    "null": None,
    "none": None,
}

_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "boolean": bool,
    "object": dict,
    "array": list,
}


class ScenarioError(ValueError):
    """Raised when a scenario file cannot be parsed into the model."""


class VariableError(KeyError):
    """Raised when ``${...}`` names a variable that is not defined yet."""

    def __str__(self) -> str:
        return self.args[0]


class AssertSyntaxError(ValueError):
    """Raised when an ``assert:`` expression is not a plain comparison."""


class WaitValueError(ValueError):
    """Raised when a ``wait_s:`` does not resolve to a positive number."""


class _Unknown:
    """Placeholder for a value only known at run time (validate only)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<unresolved>"


UNKNOWN = _Unknown()


# ── Models ─────────────────────────────────────────────────────────────────


class OnFail(str, Enum):
    """What to do when a step fails (spec §8.1)."""

    ABORT = "abort"
    CONTINUE = "continue"
    RETRY = "retry"


class StepDefaults(BaseModel):
    """Scenario-wide fallbacks for the per-step settings."""

    model_config = ConfigDict(extra="forbid")

    timeout_s: float = Field(default=30.0, gt=0)
    on_fail: OnFail = OnFail.ABORT
    retries: int = Field(default=0, ge=0)


class Step(BaseModel):
    """One scenario item: a cell call, an assertion, a timed wait, or a
    parallel block."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str | None = None
    cell: str | None = None
    action: str | None = None
    method: Literal["GET", "POST"] = "POST"
    body: dict[str, Any] = Field(default_factory=dict)
    save_as: str | None = None
    timeout_s: float | None = Field(default=None, gt=0)
    on_fail: OnFail | None = None
    retries: int | None = Field(default=None, ge=0)
    assert_expr: str | None = Field(default=None, alias="assert")
    #: Local hold: sleep this many seconds without touching any cell.
    #: A float, or a ``${...}`` string resolved at run time. The scenario
    #: language had no way to express "hold the heater for 10 s" —
    #: every timed hold used to be the operator's job in --step-mode.
    wait_s: float | str | None = None
    #: Poll-until condition on a GET call step: repeat the read every
    #: ``poll_s`` seconds until this expression is true, where
    #: ``${result.*}`` is the latest response. ``timeout_s`` bounds the
    #: whole poll, not one HTTP call. GET-only by construction — the
    #: L1 convention makes every GET a safe, repeatable probe, which is
    #: exactly what a poll needs ("heat until the plate reaches 40 °C").
    until: str | None = None
    poll_s: float = Field(default=2.0, gt=0)
    parallel: list[Step] | None = None
    #: Hold the run *before* this step and show this text to the
    #: operator. There is no wait step (spec section 8.1) and no way to
    #: express "the operator does something here", so scenarios that
    #: need a pair of hands had to be run under --step-mode, which stops
    #: after *every* step. That makes the one pause that matters
    #: indistinguishable from a dozen that do not, and an operator
    #: pressing enter thirteen times is an operator who has stopped
    #: reading. This marks the single point that needs a human.
    pause: str | None = None

    @model_validator(mode="after")
    def _exactly_one_kind(self) -> Step:
        kinds = [
            bool(self.cell or self.action),
            self.assert_expr is not None,
            self.wait_s is not None,
            self.parallel is not None,
        ]
        if sum(kinds) != 1:
            raise ValueError(
                "a step must be exactly one of: a cell call (cell + "
                "action), an 'assert', a 'wait_s', or a 'parallel' block"
            )
        if isinstance(self.wait_s, float) and self.wait_s <= 0:
            raise ValueError("'wait_s' must be a positive number")
        if self.parallel is not None:
            if not self.parallel:
                raise ValueError("'parallel' needs at least one child step")
            if any(c.parallel is not None for c in self.parallel):
                raise ValueError("'parallel' blocks cannot be nested")
            if any(c.pause is not None for c in self.parallel):
                # The gate runs before the block as a whole, so a pause
                # on a child would silently never fire. Refuse it at
                # load time rather than let a run skip the one place an
                # operator was meant to intervene.
                raise ValueError(
                    "'pause' belongs on the 'parallel' block itself, not "
                    "on a child step"
                )
        else:
            if not self.id:
                raise ValueError("every step needs an 'id'")
        if self.cell or self.action:
            if not (self.cell and self.action):
                raise ValueError("a call step needs both 'cell' and 'action'")
            if self.method == "GET" and self.body:
                raise ValueError("a GET step cannot carry a 'body'")
        if self.until is not None:
            if not (self.cell and self.action):
                raise ValueError("'until' belongs on a cell call step")
            if self.method != "GET":
                raise ValueError(
                    "'until' polls, so it is GET-only — a POST that acts "
                    "on hardware must not be repeated by a condition loop"
                )
        return self

    @property
    def kind(self) -> str:
        """``call`` | ``assert`` | ``wait`` | ``parallel``."""
        if self.parallel is not None:
            return "parallel"
        if self.assert_expr is not None:
            return "assert"
        if self.wait_s is not None:
            return "wait"
        return "call"

    def children(self) -> list[Step]:
        """The parallel block's children, or ``[self]`` for a leaf step."""
        return list(self.parallel) if self.parallel is not None else [self]

    def label(self, index: int) -> str:
        """Display id; parallel blocks get a synthetic one."""
        if self.id:
            return self.id
        return f"parallel@{index}"


class Scenario(BaseModel):
    """A whole scenario file (spec §8.1)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=SNAKE_CASE_RE)
    description: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    defaults: StepDefaults = Field(default_factory=StepDefaults)
    steps: list[Step] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> Scenario:
        seen: set[str] = set()
        for step in self.steps:
            for child in step.children():
                if child.id is None:
                    continue
                if child.id in seen:
                    raise ValueError(f"duplicate step id: {child.id!r}")
                seen.add(child.id)
        return self

    def leaf_steps(self) -> list[Step]:
        """Every call/assert step, parallel blocks flattened."""
        return [c for s in self.steps for c in s.children()]

    def index_of(self, step_id: str) -> int:
        """Top-level index holding ``step_id`` (for ``resume --from-step``).

        Raises:
            KeyError: No step with that id exists.
        """
        for i, step in enumerate(self.steps):
            if step.id == step_id or any(
                c.id == step_id for c in step.children()
            ):
                return i
        raise KeyError(step_id)


Step.model_rebuild()


def load_scenario_text(text: str) -> Scenario:
    """Parse scenario YAML text into a :class:`Scenario`.

    Raises:
        ScenarioError: The YAML is malformed or fails the model.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ScenarioError(f"YAML parse error: {exc}") from exc
    if not isinstance(raw, dict):
        raise ScenarioError("scenario must be a YAML mapping")
    try:
        return Scenario.model_validate(raw)
    except ValidationError as exc:
        raise ScenarioError(_format_validation_error(exc)) from exc


def load_scenario(path: Path) -> Scenario:
    """Read and parse a scenario file."""
    if not path.exists():
        raise ScenarioError(f"scenario file not found: {path}")
    return load_scenario_text(path.read_text(encoding="utf-8"))


def _format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        where = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"{where}: {err['msg']}")
    return "; ".join(lines)


# ── Variables ──────────────────────────────────────────────────────────────


def lookup(path: str, context: dict[str, Any]) -> Any:
    """Resolve a dotted ``${...}`` path against the variable context.

    Raises:
        VariableError: The root name is undefined or a segment is missing.
    """
    root, *rest = path.split(".")
    if root not in context:
        known = ", ".join(sorted(context)) or "-"
        raise VariableError(
            f"${{{path}}}: {root!r} is not defined yet (defined: {known})"
        )
    value = context[root]
    for part in rest:
        if isinstance(value, _Unknown):
            return UNKNOWN
        if isinstance(value, dict):
            if part not in value:
                raise VariableError(
                    f"${{{path}}}: {root!r} has no field {part!r}"
                )
            value = value[part]
        else:
            raise VariableError(
                f"${{{path}}}: cannot read {part!r} off a "
                f"{type(value).__name__}"
            )
    return value


def resolve(value: Any, context: dict[str, Any]) -> Any:
    """Substitute every ``${...}`` in a nested structure.

    A string that is *exactly* one placeholder yields the referenced value
    with its type intact (``5.0`` stays a float); otherwise the value is
    interpolated into the surrounding text.
    """
    if isinstance(value, str):
        exact = PLACEHOLDER_RE.fullmatch(value.strip())
        if exact:
            return lookup(exact.group(1), context)
        return PLACEHOLDER_RE.sub(
            lambda m: _stringify(lookup(m.group(1), context)), value
        )
    if isinstance(value, dict):
        return {k: resolve(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, context) for v in value]
    return value


def _stringify(value: Any) -> str:
    """Render a value inside a surrounding string.

    Booleans and None come out as Python literals (``True``/``False``/
    ``None``) because the main consumer is :func:`eval_assert`, whose
    grammar is a Python subset — ``"${hp.heating} == True"`` must parse.
    The JSON spellings are accepted on the other side of the comparison.
    """
    return str(value)


def referenced_paths(value: Any) -> set[str]:
    """Every ``${...}`` path used anywhere inside a nested structure."""
    if isinstance(value, str):
        return set(PLACEHOLDER_RE.findall(value))
    if isinstance(value, dict):
        return (
            set().union(*(referenced_paths(v) for v in value.values()))
            if value
            else set()
        )
    if isinstance(value, list):
        return (
            set().union(*(referenced_paths(v) for v in value))
            if value
            else set()
        )
    return set()


def eval_assert(expression: str) -> bool:
    """Evaluate an already-interpolated ``assert:`` expression.

    Only comparisons, boolean operators and literals are allowed — no
    names, calls or attribute access survive the AST whitelist, so a
    scenario file cannot execute code.

    Raises:
        AssertSyntaxError: The expression is not parseable or uses a
            construct outside the whitelist.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise AssertSyntaxError(f"{expression!r}: {exc.msg}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_ASSERT_NODES):
            raise AssertSyntaxError(
                f"{expression!r}: {type(node).__name__} is not allowed in "
                "an assert (comparisons and literals only)"
            )
        if isinstance(node, ast.Name) and node.id not in ASSERT_NAMES:
            raise AssertSyntaxError(
                f"{expression!r}: {node.id!r} is not a literal; an assert "
                f"may only name {', '.join(sorted(ASSERT_NAMES))}"
            )
    return bool(
        eval(
            compile(tree, "<assert>", "eval"),
            {"__builtins__": {}, **ASSERT_NAMES},
        )
    )


# ── OpenAPI lookups ────────────────────────────────────────────────────────


def _deref(document: dict[str, Any], node: Any) -> dict[str, Any]:
    """Follow ``$ref`` / single-entry ``allOf`` into a concrete schema."""
    seen = 0
    while isinstance(node, dict) and seen < MAX_REF_DEPTH:
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            target: Any = document
            for part in ref.removeprefix("#/").split("/"):
                target = (target or {}).get(part, {})
            node = target
        elif isinstance(node.get("allOf"), list) and len(node["allOf"]) == 1:
            node = node["allOf"][0]
        else:
            break
        seen += 1
    return node if isinstance(node, dict) else {}


def operation(
    document: dict[str, Any], action: str, method: str
) -> dict[str, Any] | None:
    """The OpenAPI operation object for ``/v1/<action>`` + method."""
    path_item = document.get("paths", {}).get(API_PREFIX + action.lstrip("/"))
    if not isinstance(path_item, dict):
        return None
    op = path_item.get(method.lower())
    return op if isinstance(op, dict) else None


def path_methods(document: dict[str, Any], action: str) -> tuple[str, ...]:
    """Methods the cell exposes on ``/v1/<action>``."""
    path_item = document.get("paths", {}).get(API_PREFIX + action.lstrip("/"))
    if not isinstance(path_item, dict):
        return ()
    return tuple(
        m.upper() for m in path_item if m in ("get", "post", "put", "delete")
    )


def known_actions(document: dict[str, Any]) -> tuple[str, ...]:
    """Every ``/v1`` action the cell exposes, for error messages."""
    return tuple(
        sorted(
            p.removeprefix(API_PREFIX)
            for p in document.get("paths", {})
            if p.startswith(API_PREFIX)
        )
    )


def request_schema(
    document: dict[str, Any], op: dict[str, Any]
) -> dict[str, Any] | None:
    """The JSON request-body schema of an operation, refs resolved."""
    body = op.get("requestBody")
    if not isinstance(body, dict):
        return None
    content = body.get("content", {}).get("application/json", {})
    schema = content.get("schema")
    if schema is None:
        return None
    return _deref(document, schema)


def check_body(
    document: dict[str, Any],
    schema: dict[str, Any] | None,
    body: dict[str, Any],
) -> list[str]:
    """Compare a step body against the endpoint's request schema.

    Values that come from a not-yet-known variable are checked for
    presence only, never for type.

    Returns:
        Human-readable mismatch messages (empty when the body fits).
    """
    problems: list[str] = []
    if schema is None:
        if body:
            problems.append("endpoint takes no request body")
        return problems
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    for missing in sorted(required - set(body)):
        problems.append(f"missing required field {missing!r}")
    for key, value in body.items():
        if key not in properties:
            allowed = ", ".join(sorted(properties)) or "-"
            problems.append(
                f"unknown field {key!r} (endpoint takes: {allowed})"
            )
            continue
        if isinstance(value, _Unknown):
            continue
        problems.extend(_check_type(document, key, properties[key], value))
    return problems


def _check_type(
    document: dict[str, Any], key: str, prop: Any, value: Any
) -> list[str]:
    schema = _deref(document, prop)
    for variant in ("anyOf", "oneOf"):
        if variant in schema:
            return []  # optional/union field: presence check only
    json_type = schema.get("type")
    if json_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"field {key!r} must be a number, got {value!r}"]
        return []
    if json_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"field {key!r} must be an integer, got {value!r}"]
        return []
    expected = _JSON_TYPES.get(str(json_type)) if json_type else None
    if expected and not isinstance(value, expected):
        return [f"field {key!r} must be a {json_type}, got {value!r}"]
    return []


# ── Validator (dry run) ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One problem found by the dry run."""

    code: str
    step_id: str | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "step_id": self.step_id,
            "message": self.message,
        }

    def __str__(self) -> str:
        where = f" [{self.step_id}]" if self.step_id else ""
        return f"{self.code}{where}: {self.message}"


async def validate_scenario(
    scenario: Scenario,
    registry: Registry,
    client: CellClient,
    *,
    params: dict[str, Any] | None = None,
) -> list[ValidationIssue]:
    """Dry-run a scenario against the registry and the cells' OpenAPI.

    Args:
        scenario: The parsed scenario.
        registry: Cell address book.
        client: Used for ``GET /openapi.json`` only — no device is touched.
        params: Overrides merged over ``scenario.params``.

    Returns:
        Issues in scenario order; empty means the scenario is runnable.
    """
    issues: list[ValidationIssue] = []
    merged = {**scenario.params, **(params or {})}
    context: dict[str, Any] = {PARAMS_ROOT: merged}
    documents: dict[str, dict[str, Any] | None] = {}
    reported: set[str] = set()

    for index, step in enumerate(scenario.steps):
        if step.kind == "parallel":
            issues.extend(_check_parallel_cells(step, index))
        produced: list[str] = []
        for child in step.children():
            issues.extend(
                await _validate_leaf(
                    child, registry, client, documents, context, reported
                )
            )
            if child.save_as:
                produced.append(child.save_as)
        for name in produced:
            context.setdefault(name, UNKNOWN)
    return issues


def resolve_wait_s(step: Step, context: dict[str, Any]) -> float:
    """Resolve a wait step's duration to concrete positive seconds.

    Args:
        step: A ``wait`` step.
        context: Variable context for ``${...}`` resolution.

    Returns:
        The number of seconds to hold.

    Raises:
        VariableError: A placeholder names an undefined variable.
        WaitValueError: The resolved value is not a positive number.
    """
    assert step.wait_s is not None
    value = resolve(step.wait_s, context)
    if isinstance(value, _Unknown):
        raise WaitValueError(f"step {step.id!r}: wait_s is unresolved")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WaitValueError(
            f"step {step.id!r}: wait_s must be a number, got {value!r}"
        )
    if value <= 0:
        raise WaitValueError(
            f"step {step.id!r}: wait_s must be positive, got {value!r}"
        )
    return float(value)


def _check_wait(step: Step, context: dict[str, Any]) -> list[ValidationIssue]:
    """Dry-run check of a wait step's duration, where it is knowable.

    A duration that hangs off a run-time variable (:data:`UNKNOWN`) is
    checked for presence only, like an unknown body field.
    """
    assert step.wait_s is not None
    try:
        value = resolve(step.wait_s, context)
    except VariableError:
        return []  # already reported as var_order above
    if isinstance(value, _Unknown):
        return []
    try:
        resolve_wait_s(step, context)
    except WaitValueError as exc:
        return [ValidationIssue("body_mismatch", step.id, str(exc))]
    return []


def _check_parallel_cells(step: Step, index: int) -> list[ValidationIssue]:
    seen: set[str] = set()
    issues: list[ValidationIssue] = []
    for child in step.children():
        if child.cell is None:
            continue
        if child.cell in seen:
            issues.append(
                ValidationIssue(
                    "parallel_duplicate_cell",
                    step.label(index),
                    f"cell {child.cell!r} appears twice in one parallel "
                    "block; a cell runs one step at a time",
                )
            )
        seen.add(child.cell)
    return issues


async def _validate_leaf(
    step: Step,
    registry: Registry,
    client: CellClient,
    documents: dict[str, dict[str, Any] | None],
    context: dict[str, Any],
    reported: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if step.kind == "call":
        source: Any = step.body
    elif step.kind == "wait":
        source = step.wait_s
    else:
        source = step.assert_expr
    paths = set(referenced_paths(source))
    if step.until is not None:
        # ``result`` is defined by the poll itself, not the context.
        paths |= {
            p
            for p in referenced_paths(step.until)
            if p.split(".")[0] != UNTIL_RESULT_ROOT
        }
    for path in sorted(paths):
        try:
            lookup(path, context)
        except VariableError as exc:
            issues.append(ValidationIssue("var_order", step.id, str(exc)))
    if step.kind == "wait":
        issues.extend(_check_wait(step, context))
        return issues
    if step.kind != "call":
        return issues

    assert step.cell is not None and step.action is not None
    if step.cell not in registry:
        issues.append(
            ValidationIssue(
                "unknown_cell",
                step.id,
                f"cell {step.cell!r} is not in the registry "
                f"({', '.join(registry.names()) or 'empty'})",
            )
        )
        return issues

    document = await _openapi(step.cell, client, documents)
    if document is None:
        # One issue per cell, not per step: an offline cell would
        # otherwise bury every other finding.
        if step.cell not in reported:
            reported.add(step.cell)
            issues.append(
                ValidationIssue(
                    "cell_unreachable",
                    step.id,
                    f"cell {step.cell!r} did not serve /openapi.json; "
                    "start it and re-validate",
                )
            )
        return issues

    op = operation(document, step.action, step.method)
    if op is None:
        methods = path_methods(document, step.action)
        if methods:
            detail = (
                f"action {step.action!r} exists but not as {step.method}; "
                f"cell offers {', '.join(methods)}"
            )
        else:
            detail = (
                f"action {step.action!r} is not in {step.cell}'s OpenAPI; "
                f"known: {', '.join(known_actions(document)) or '-'}"
            )
        issues.append(ValidationIssue("unknown_action", step.id, detail))
        return issues

    body = _resolve_for_validation(step.body, context)
    for problem in check_body(document, request_schema(document, op), body):
        issues.append(ValidationIssue("body_mismatch", step.id, problem))
    return issues


def _resolve_for_validation(
    body: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """Resolve what is knowable now; unknown vars become :data:`UNKNOWN`."""
    resolved: dict[str, Any] = {}
    for key, value in body.items():
        try:
            resolved[key] = resolve(value, context)
        except VariableError:
            resolved[key] = UNKNOWN
    return resolved


async def _openapi(
    cell: str,
    client: CellClient,
    documents: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    if cell not in documents:
        try:
            documents[cell] = await client.openapi(cell)
        except CellCallError:
            documents[cell] = None
    return documents[cell]
