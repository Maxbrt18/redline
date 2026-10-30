"""What the coach pays to read: a word ceiling per file, inside a reading tier.

The tiers are instance data (`[[vault.budget]]` in `redline.toml`): which files the
agent reads, and how often, belongs to the vault's own instructions. A tier says how
often its files are paid for (on every message, or only when a judgement is made), so
two tiers never compensate each other. A ceiling per file names the culprit of an
overflow; the tier total is the real constraint.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Tier:
    name: str
    total: int
    #: (vault-relative path, word ceiling)
    files: tuple[tuple[str, int], ...]


def ceiling_errors(tiers: tuple[Tier, ...]) -> list[str]:
    """A tier whose file ceilings add up to more than its total, or a file in two tiers."""
    problems = [f"tier {tier.name!r}: sum of ceilings {sum(words for _, words in tier.files)} > total {tier.total}"
                for tier in tiers if sum(words for _, words in tier.files) > tier.total]
    paths = [path for tier in tiers for path, _ in tier.files]
    problems += [f"{path} is budgeted in two tiers"
                 for path in sorted({path for path in paths if paths.count(path) > 1})]
    return problems


@dataclass
class Audit:
    tiers: tuple[Tier, ...]
    counts: dict = field(default_factory=dict)

    @property
    def over(self) -> list:
        """[(file, words, ceiling)] for each file over its ceiling."""
        return [(path, self.counts[path], ceiling) for tier in self.tiers
                for path, ceiling in tier.files if self.counts[path] > ceiling]

    @property
    def totals(self) -> dict:
        return {tier.name: sum(self.counts[path] for path, _ in tier.files)
                for tier in self.tiers}

    @property
    def over_total(self) -> list:
        """[(tier, words, total)] for each tier over its total."""
        return [(tier.name, self.totals[tier.name], tier.total) for tier in self.tiers
                if self.totals[tier.name] > tier.total]

    @property
    def near_total(self) -> bool:
        """Every tier within its total, one of them with less than 5 % left."""
        return not self.over and not self.over_total and any(
            self.totals[tier.name] > tier.total * 0.95 for tier in self.tiers)

    @property
    def detail(self) -> str:
        return "  ".join(
            f"[{tier.name} {self.totals[tier.name]}/{tier.total}] "
            + " · ".join(f"{path} {self.counts[path]}/{ceiling}"
                         for path, ceiling in tier.files)
            for tier in self.tiers)

    def problems(self) -> list:
        """Each overflow, named with the tier that says how often it is paid."""
        tier_of = {path: tier.name for tier in self.tiers for path, _ in tier.files}
        lines = [f"{path}: {words} words > {ceiling} -- tier {tier_of[path]!r}"
                 for path, words, ceiling in self.over]
        lines += [f"tier {tier!r}: {words} words > {total}"
                  for tier, words, total in self.over_total]
        return lines


def words(text: str) -> int:
    return len(text.split())


def audit(tiers: tuple[Tier, ...], texts: dict) -> Audit:
    """`{path: text}` -> the audit. A missing file counts as zero."""
    result = Audit(tiers)
    for tier in tiers:
        for path, _ in tier.files:
            result.counts[path] = words(texts.get(path) or "")
    return result
