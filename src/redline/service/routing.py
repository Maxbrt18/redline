"""Routing policy for agent turns.

The bot never knows provider model names. It asks for a profile (triage, N0-N3,
deep analysis or heavy proactive) and this module supplies the matching attempt
plan. The Claude and Codex adapters consume the same ``RunSpec``; only their
default model names differ here.

The operator changes a profile without a release: `REDLINE_ROUTING` names a TOML
file whose `[routing.<ROUTE>]` tables override the defaults, checked at startup:

    [routing.N2]
    model = "sonnet"
    effort = "high"
    fallback = { model = "opus", effort = "high" }
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from redline.model.errors import ConfigError

from .agent import RunSpec, SessionMode


class RoutingError(ConfigError):
    """The routing file or a profile it declares is unusable."""


class Route(StrEnum):
    """Stable identifiers of the work profiles."""

    TRIAGE = "TRIAGE"
    N0 = "N0"
    N1 = "N1"
    N2 = "N2"
    N3 = "N3"
    DEEP = "DEEP"
    HEAVY = "HEAVY"

    @classmethod
    def normalize(cls, value: str | Route) -> Route:
        raw = value.value if isinstance(value, cls) else str(value).strip().upper()
        if raw not in {
            cls.TRIAGE, cls.N0, cls.N1, cls.N2, cls.N3, cls.DEEP, cls.HEAVY,
        }:
            raise RoutingError(f"unknown route: {value!r}")
        return cls(raw)


@dataclass(frozen=True)
class RoutePlan:
    """Primary attempt and bounded fallbacks of a route."""

    route: Route
    primary: RunSpec
    fallbacks: tuple[RunSpec, ...] = ()

    @property
    def attempts(self) -> tuple[RunSpec, ...]:
        return (self.primary, *self.fallbacks)

    def with_session_mode(self, mode: SessionMode) -> RoutePlan:
        return replace(
            self,
            primary=replace(self.primary, session_mode=mode),
            fallbacks=tuple(replace(spec, session_mode=mode) for spec in self.fallbacks),
        )

    def with_read_only(self, read_only: bool = True) -> RoutePlan:
        return replace(
            self,
            primary=replace(self.primary, read_only=read_only),
            fallbacks=tuple(replace(spec, read_only=read_only) for spec in self.fallbacks),
        )

    def with_structured_reply(self) -> RoutePlan:
        """Every attempt returns `{send, text}` instead of free prose."""
        return replace(
            self,
            primary=replace(self.primary, structured_reply=True),
            fallbacks=tuple(replace(spec, structured_reply=True) for spec in self.fallbacks),
        )


@dataclass(frozen=True)
class RoutingConfig:
    """Immutable configuration of all routes and their fallbacks."""

    provider: str
    plans: Mapping[Route, RoutePlan]

    def __post_init__(self) -> None:
        provider = self.provider.strip().lower()
        if provider not in _PROVIDER_DEFAULTS:
            raise RoutingError(f"unknown provider: {self.provider!r}")
        normalized = {}
        for route, plan in self.plans.items():
            normalized_route = Route.normalize(route)
            normalized_plan = (
                plan if plan.route == normalized_route
                else replace(plan, route=normalized_route)
            )
            normalized[normalized_route] = replace(
                normalized_plan,
                primary=_normalize_spec(normalized_plan.primary),
                fallbacks=tuple(
                    _normalize_spec(spec) for spec in normalized_plan.fallbacks),
            )
        missing = set(_ROUTE_ORDER) - set(normalized)
        if missing:
            raise RoutingError("missing routes: " + ", ".join(sorted(missing)))
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "plans", MappingProxyType(normalized))

    def plan(self, route: str | Route) -> RoutePlan:
        return self.plans[Route.normalize(route)]

    def for_depth(self, depth: str, *, deep_request: bool = False) -> RoutePlan:
        if deep_request:
            return self.plan(Route.DEEP)
        try:
            return self.plan(depth)
        except RoutingError:
            return self.plan(Route.N1)

    def describe(self, route: str | Route) -> str:
        plan = self.plan(route)
        primary = _describe_spec(plan.primary)
        fallback = ", ".join(_describe_spec(spec) for spec in plan.fallbacks)
        return primary if not fallback else f"{primary} (fallback: {fallback})"

    def validate(self) -> None:
        """Also validate configurations assembled without ``from_file``."""
        for route in _ROUTE_ORDER:
            plan = self.plan(route)
            for index, spec in enumerate(plan.attempts):
                _validate_target(self.provider, route, spec, fallback=index > 0)

    @classmethod
    def default(
        cls,
        provider: str,
        *,
        agent_timeout: int = 600,
        agent_timeout_deep: int = 1800,
        agent_timeout_heavy: int = 5400,
        triage_timeout: int = 10,
    ) -> RoutingConfig:
        """Build the canonical profiles without consulting the environment."""
        normalized_provider = provider.strip().lower()
        try:
            defaults = _PROVIDER_DEFAULTS[normalized_provider]
        except KeyError:
            raise RoutingError(f"unknown provider: {provider!r}") from None
        timeouts = {
            Route.TRIAGE: triage_timeout,
            Route.N0: agent_timeout,
            Route.N1: agent_timeout,
            # N2/N3 reasoning keeps the deep watchdog: cost outweighs latency, and
            # useful work already started is not interrupted.
            Route.N2: agent_timeout_deep,
            Route.N3: agent_timeout_deep,
            Route.DEEP: agent_timeout_deep,
            Route.HEAVY: agent_timeout_heavy,
        }
        modes = {
            Route.TRIAGE: SessionMode.ISOLATED,
            Route.N0: SessionMode.CONTINUE,
            Route.N1: SessionMode.CONTINUE,
            Route.N2: SessionMode.CONTINUE,
            Route.N3: SessionMode.CONTINUE,
            Route.DEEP: SessionMode.FORK,
            Route.HEAVY: SessionMode.ISOLATED,
        }
        plans = {}
        for route in _ROUTE_ORDER:
            primary, fallback = defaults[route]
            read_only = route == Route.TRIAGE
            plans[route] = RoutePlan(
                route,
                _spec(primary, timeouts[route], modes[route], read_only=read_only),
                () if fallback is None else
                (_spec(fallback, timeouts[route], modes[route], read_only=read_only),),
            )
        return cls(normalized_provider, plans)

    @classmethod
    def from_file(
        cls,
        provider: str,
        path: Path | None,
        *,
        agent_timeout: int,
        agent_timeout_deep: int,
        agent_timeout_heavy: int,
        triage_timeout: int,
    ) -> RoutingConfig:
        """The default profiles, overridden by the `[routing.<ROUTE>]` tables of `path`."""
        base = cls.default(
            provider,
            agent_timeout=agent_timeout,
            agent_timeout_deep=agent_timeout_deep,
            agent_timeout_heavy=agent_timeout_heavy,
            triage_timeout=triage_timeout,
        )
        overrides = _read_overrides(path) if path else {}
        plans = {}
        for route in _ROUTE_ORDER:
            current = base.plan(route)
            table = overrides.get(route, {})
            primary = replace(current.primary, model=table.get("model", current.primary.model),
                              effort=table.get("effort", current.primary.effort))
            _validate_target(base.provider, route, primary)
            fallbacks: tuple[RunSpec, ...] = ()
            # Triage has no fallback: waiting for a second classifier after a timeout
            # would leave up to a minute of "seen" with no sign of life. A file that
            # describes one is refused rather than ignored.
            if "fallback" in table and not current.fallbacks:
                raise RoutingError(f"[routing.{route.value}] fallback: this route has none")
            for fallback_default in current.fallbacks:
                given = table.get("fallback", {})
                fallback = replace(fallback_default,
                                   model=given.get("model", fallback_default.model),
                                   effort=given.get("effort", fallback_default.effort))
                _validate_target(base.provider, route, fallback, fallback=True)
                fallbacks += (fallback,)
            plans[route] = RoutePlan(route, primary, fallbacks)
        return cls(base.provider, plans)


_TARGET_KEYS = frozenset({"model", "effort"})


def _read_overrides(path: Path) -> dict[Route, dict]:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RoutingError(f"REDLINE_ROUTING: {path} unreadable ({exc})") from None
    unknown = set(payload) - {"routing"}
    if unknown or not isinstance(payload.get("routing", {}), dict):
        raise RoutingError(f"{path}: only [routing.<ROUTE>] tables are allowed")
    overrides = {}
    for name, table in payload.get("routing", {}).items():
        route = Route.normalize(name)
        where = f"[routing.{route.value}]"
        if not isinstance(table, dict) or set(table) - _TARGET_KEYS - {"fallback"}:
            raise RoutingError(f"{where}: allowed keys are model, effort and fallback")
        fallback = table.get("fallback", {})
        if not isinstance(fallback, dict) or set(fallback) - _TARGET_KEYS:
            raise RoutingError(f"{where} fallback: allowed keys are model and effort")
        for key, value in [*table.items(), *fallback.items()]:
            if key != "fallback" and not (isinstance(value, str) and value.strip()):
                raise RoutingError(f"{where} {key}: a non-empty string is expected")
        overrides[route] = table
    return overrides


_ROUTE_ORDER = (
    Route.TRIAGE, Route.N0, Route.N1, Route.N2, Route.N3, Route.DEEP, Route.HEAVY,
)


@dataclass(frozen=True)
class _TargetDefault:
    model: str
    effort: str


def _spec(target: _TargetDefault, timeout: int, mode: SessionMode, *, read_only: bool) -> RunSpec:
    return RunSpec(
        model=target.model,
        effort=target.effort,
        timeout=timeout,
        session_mode=mode,
        read_only=read_only,
    )


# Triage is a fast classifier: low effort and no fallback. A missing verdict falls
# back to the deterministic default `Triage()`, which locks nothing.
_PROVIDER_DEFAULTS: dict[str, dict[Route, tuple[_TargetDefault, _TargetDefault | None]]] = {
    "codex": {
        Route.TRIAGE: (_TargetDefault("gpt-6-luna", "low"), None),
        Route.N0: (_TargetDefault("gpt-6-luna", "medium"),
                   _TargetDefault("gpt-6-luna", "high")),
        Route.N1: (_TargetDefault("gpt-6-luna", "high"),
                   _TargetDefault("gpt-6-luna", "high")),
        Route.N2: (_TargetDefault("gpt-6-sol", "medium"),
                   _TargetDefault("gpt-6-luna", "max")),
        Route.N3: (_TargetDefault("gpt-6-sol", "high"),
                   _TargetDefault("gpt-6-luna", "max")),
        Route.DEEP: (_TargetDefault("gpt-6-sol", "high"),
                     _TargetDefault("gpt-6-luna", "max")),
        Route.HEAVY: (_TargetDefault("gpt-6-sol", "high"),
                      _TargetDefault("gpt-6-luna", "max")),
    },
    # Haiku is pinned: the `haiku` alias of the stable CLI still resolves to an
    # older, far more expensive Haiku. Sonnet and Opus follow their alias, which the
    # nightly preflight resolves and displays.
    # N0 to N2 share Sonnet so the continued session keeps its cache, which is
    # specific to each model. A fallback changes model, otherwise it would hit the
    # same quota. N3 takes the Opus default effort; /deep and heavy proactive keep
    # high.
    "claude": {
        Route.TRIAGE: (_TargetDefault("claude-haiku-5-5", "low"), None),
        Route.N0: (_TargetDefault("sonnet", "low"),
                   _TargetDefault("claude-haiku-5-5", "low")),
        Route.N1: (_TargetDefault("sonnet", "low"),
                   _TargetDefault("opus", "medium")),
        Route.N2: (_TargetDefault("sonnet", "high"),
                   _TargetDefault("opus", "high")),
        Route.N3: (_TargetDefault("opus", "medium"),
                   _TargetDefault("sonnet", "high")),
        Route.DEEP: (_TargetDefault("opus", "high"),
                     _TargetDefault("sonnet", "high")),
        Route.HEAVY: (_TargetDefault("opus", "high"),
                      _TargetDefault("sonnet", "high")),
    },
}

_CLAUDE_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
_CODEX_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})
_EFFORTS = {"claude": _CLAUDE_EFFORTS, "codex": _CODEX_EFFORTS}


def default_models(provider: str) -> frozenset[str]:
    """The models named by a provider's canonical profiles, all routes and fallbacks
    combined: the only model list in the repository, which the bench re-reads to bound
    what it may pay for."""
    return frozenset(
        target.model
        for primary, fallback in _PROVIDER_DEFAULTS[provider.strip().lower()].values()
        for target in (primary, fallback) if target is not None and target.model)

def _normalize_spec(spec: RunSpec) -> RunSpec:
    effort = spec.effort.strip().lower() if spec.effort else None
    return replace(spec, effort=effort)


def _validate_target(provider: str, route: Route, spec: RunSpec, *,
                     fallback: bool = False) -> None:
    effort = (spec.effort or "").lower()
    valid = _EFFORTS[provider]
    if effort and effort not in valid:
        where = f"[routing.{route.value}]" + (" fallback" if fallback else "")
        raise RoutingError(
            f"{where} effort: {spec.effort!r} is not a {provider} effort "
            f"(expected {', '.join(sorted(valid))})")


def _describe_spec(spec: RunSpec) -> str:
    model = spec.model or "default"
    effort = spec.effort or "default"
    return f"{model}/{effort}"
