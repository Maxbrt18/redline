"""Routing policy for agent turns.

The bot never knows provider model names. It asks for a profile (triage, N0-N3,
deep analysis or heavy proactive) and this module supplies the matching attempt
plan. The Claude and Codex adapters consume the same ``RunSpec``; only their
default model names differ here.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType

from .agent import RunSpec, SessionMode


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
            raise ValueError(f"route inconnue : {value!r}")
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
            raise ValueError(f"fournisseur inconnu : {self.provider!r}")
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
            raise ValueError("routes manquantes : " + ", ".join(sorted(missing)))
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "plans", MappingProxyType(normalized))

    def plan(self, route: str | Route) -> RoutePlan:
        return self.plans[Route.normalize(route)]

    def for_depth(self, depth: str, *, deep_request: bool = False) -> RoutePlan:
        if deep_request:
            return self.plan(Route.DEEP)
        try:
            return self.plan(depth)
        except ValueError:
            return self.plan(Route.N1)

    def describe(self, route: str | Route) -> str:
        plan = self.plan(route)
        primary = _describe_spec(plan.primary)
        fallback = ", ".join(_describe_spec(spec) for spec in plan.fallbacks)
        return primary if not fallback else f"{primary} (repli : {fallback})"

    def validate(self) -> None:
        """Also validate configurations assembled without ``from_env``."""
        for route in _ROUTE_ORDER:
            plan = self.plan(route)
            for index, spec in enumerate(plan.attempts):
                suffix = "" if index == 0 else "FALLBACK_"
                _validate_target(
                    self.provider, route, spec, f"AGENT_{route.value}_{suffix}")

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
            raise ValueError(f"fournisseur inconnu : {provider!r}") from None
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
    def from_env(
        cls,
        provider: str,
        *,
        agent_timeout: int,
        agent_timeout_deep: int,
        agent_timeout_heavy: int,
        triage_timeout: int,
    ) -> RoutingConfig:
        """Build the profiles, then apply the ``AGENT_<ROUTE>_*`` variables."""
        base = cls.default(
            provider,
            agent_timeout=agent_timeout,
            agent_timeout_deep=agent_timeout_deep,
            agent_timeout_heavy=agent_timeout_heavy,
            triage_timeout=triage_timeout,
        )
        plans = {}
        for route in _ROUTE_ORDER:
            current = base.plan(route)
            prefix = f"AGENT_{route.value}_"
            primary_model = _envtext(prefix + "MODEL", current.primary.model)
            primary_effort = _envtext(prefix + "EFFORT", current.primary.effort)
            primary = replace(current.primary, model=primary_model, effort=primary_effort)
            _validate_target(base.provider, route, primary, prefix)
            fallbacks: tuple[RunSpec, ...] = ()
            # Triage has no fallback: waiting for a second classifier after a timeout
            # would leave up to a minute of "seen" with no sign of life. An override
            # that described one is rejected rather than ignored.
            if not current.fallbacks:
                for suffix in ("FALLBACK_MODEL", "FALLBACK_EFFORT"):
                    if _envtext(prefix + suffix, None) is not None:
                        raise SystemExit(
                            f"{prefix}{suffix} invalide : la route {route.value} "
                            "n'a pas de repli")
            for fallback_default in current.fallbacks:
                fallback = replace(
                    fallback_default,
                    model=_envtext(prefix + "FALLBACK_MODEL", fallback_default.model),
                    effort=_envtext(prefix + "FALLBACK_EFFORT", fallback_default.effort))
                _validate_target(base.provider, route, fallback, prefix + "FALLBACK_")
                fallbacks += (fallback,)
            plans[route] = RoutePlan(route, primary, fallbacks)
        return cls(base.provider, plans)


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


# Sol is deliberately limited to medium/high: Sol Max is excluded by design.
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
_CODEX_MODEL_EFFORTS = {
    "gpt-6-luna": frozenset({"none", "low", "medium", "high", "xhigh", "max"}),
    # Product policy deliberately excludes the two most expensive levels.
    "gpt-6-sol": frozenset({"none", "low", "medium", "high", "xhigh"}),
}


def default_models(provider: str) -> frozenset[str]:
    """The models named by a provider's canonical profiles, all routes and fallbacks
    combined: the only model list in the repository, which the bench re-reads to bound
    what it may pay for."""
    return frozenset(
        target.model
        for primary, fallback in _PROVIDER_DEFAULTS[provider.strip().lower()].values()
        for target in (primary, fallback) if target is not None and target.model)

def _envtext(name: str, default: str | None) -> str | None:
    raw = os.environ.get(name)
    return default if raw is None or not raw.strip() else raw.strip()


def _normalize_spec(spec: RunSpec) -> RunSpec:
    effort = spec.effort.strip().lower() if spec.effort else None
    return replace(spec, effort=effort)


def _valid_efforts(provider: str, model: str) -> frozenset[str]:
    if provider == "claude":
        return _CLAUDE_EFFORTS
    normalized_model = model.lower().replace("_", "-")
    for prefix, efforts in _CODEX_MODEL_EFFORTS.items():
        if normalized_model == prefix or normalized_model.startswith(prefix + "-"):
            return efforts
    return _CODEX_EFFORTS


def _validate_target(
    provider: str, route: Route, spec: RunSpec, prefix: str,
) -> None:
    model = (spec.model or "").lower().replace("_", "-")
    if provider == "codex" and not any(
            model == allowed or model.startswith(allowed + "-")
            for allowed in _CODEX_MODEL_EFFORTS):
        raise SystemExit(
            f"{prefix}MODEL interdit : seules Luna et Sol sont autorisées "
            f"pour le bot Codex ({spec.model!r})")
    effort = (spec.effort or "").lower()
    valid_efforts = _valid_efforts(provider, model)
    if model.startswith("gpt-6-sol") and effort in {"max", "ultra"}:
        raise SystemExit(
            f"{prefix}EFFORT interdit : Sol Max est désactivé pour la route "
            f"{route.value}")
    if effort and effort not in valid_efforts:
        raise SystemExit(
            f"{prefix}EFFORT invalid pour {provider}/{spec.model or 'défaut'} : "
            f"{spec.effort!r} (attendu : {', '.join(sorted(valid_efforts))})")


def _describe_spec(spec: RunSpec) -> str:
    model = spec.model or "défaut"
    effort = spec.effort or "défaut"
    return f"{model}/{effort}"
