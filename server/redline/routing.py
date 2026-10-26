"""Politique de routage des tours agent.

Le bot ne connaît jamais les noms de modèles des fournisseurs. Il demande un
profil (triage, N0–N3, analyse profonde ou proactif lourd) et ce module fournit le
plan de tentatives correspondant. Les adaptateurs Claude et Codex consomment le
même ``RunSpec`` ; seuls leurs noms de modèle par défaut diffèrent ici.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType

from .agent import RunSpec, SessionMode


class Route(StrEnum):
    """Identifiants stables des profils de travail."""

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
    """Tentative principale et replis bornés d'une route."""

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
        """Toutes les tentatives rendent `{send, text}` au lieu d'une prose libre."""
        return replace(
            self,
            primary=replace(self.primary, structured_reply=True),
            fallbacks=tuple(replace(spec, structured_reply=True) for spec in self.fallbacks),
        )


@dataclass(frozen=True)
class RoutingConfig:
    """Configuration immuable de toutes les routes et de leur fallback."""

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
        """Vérifie aussi les configurations assemblées sans ``from_env``."""
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
        """Construit les profils canoniques sans consulter l'environnement."""
        normalized_provider = provider.strip().lower()
        try:
            defaults = _PROVIDER_DEFAULTS[normalized_provider]
        except KeyError:
            raise ValueError(f"fournisseur inconnu : {provider!r}") from None
        timeouts = {
            Route.TRIAGE: triage_timeout,
            Route.N0: agent_timeout,
            Route.N1: agent_timeout,
            # Le raisonnement N2/N3 garde le watchdog profond : le coût prime
            # sur la latence, sans interrompre un travail utile déjà commencé.
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
        """Construit les profils puis applique les variables ``AGENT_<ROUTE>_*``."""
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
            # Le triage n'a pas de repli : attendre un second classifieur après un
            # délai dépassé coûtait jusqu'à une minute de « Vu » sans signe de vie.
            # Une surcharge qui en décrirait un est refusée plutôt qu'ignorée.
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


# Sol est volontairement limité à medium/high : Sol Max est exclu par conception.
# Le triage est un classifieur rapide : effort bas et aucun repli. Un verdict absent
# retombe sur le défaut déterministe `Triage()`, qui ne verrouille rien.
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
    # Haiku est épinglé : l'alias `haiku` du CLI stable résout encore Haiku 4.5,
    # plus ancien et dix fois plus cher (prévol du 2026-10-08). Sonnet et Opus
    # suivent leur alias, que le prévol nocturne résout et affiche.
    # N0 à N2 partagent Sonnet : la session continue garde ainsi son cache, qui
    # est propre à chaque modèle. Un repli change de modèle, sinon il retombe sur
    # le même quota. N3 prend l'effort par défaut d'Opus 5.5, recalibré ; /deep et
    # le proactif lourd gardent high.
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
    # La politique produit exclut volontairement les deux niveaux les plus chers.
    "gpt-6-sol": frozenset({"none", "low", "medium", "high", "xhigh"}),
}


def default_models(provider: str) -> frozenset[str]:
    """Les modèles que les profils canoniques d'un fournisseur nomment, toutes routes et
    replis confondus : la seule liste de modèles du dépôt, que le banc relit pour borner
    ce qu'il a le droit de payer."""
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
