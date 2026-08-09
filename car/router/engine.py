"""The deterministic, provider-independent CAR decision engine."""

from car.repository.models import RepositoryState
from car.router.analysis import analyze_task
from car.router.models import (
    Complexity,
    RiskAssessment,
    RiskLevel,
    Route,
    RoutingDecision,
    RoutingPolicy,
    ScopeSize,
    TaskAnalysis,
    TaskCategory,
    TaskRequest,
    UserMode,
)


def assess_risk(analysis: TaskAnalysis, repository: RepositoryState) -> RiskAssessment:
    """Calculate a documented heuristic score, not a model probability."""
    score = 0.10
    indicators: list[str] = []
    hard_categories = RoutingPolicy().hard_codex_categories
    hard_matches = set(analysis.categories).intersection(hard_categories)
    if hard_matches:
        score += 0.70
        indicators.append(
            "hard-risk category: " + ", ".join(sorted(item.value for item in hard_matches))
        )
    if TaskCategory.DEPLOYMENT in analysis.categories:
        score += 0.70
        indicators.append("production deployment")
    if TaskCategory.PUBLIC_API in analysis.categories:
        score += 0.15
        indicators.append("public API impact")
    if TaskCategory.DEPENDENCY_CHANGE in analysis.categories:
        score += 0.12
        indicators.append("dependency change")
    if analysis.scope.size == ScopeSize.MEDIUM:
        score += 0.20
        indicators.append("medium estimated scope")
    elif analysis.scope.size == ScopeSize.LARGE:
        score += 0.30
        indicators.append("large estimated scope")
    elif analysis.scope.size == ScopeSize.UNKNOWN:
        score += 0.25
        indicators.append("unknown scope")
    if analysis.complexity == Complexity.HIGH:
        score += 0.10
    if repository.git.dirty:
        score += 0.05
        indicators.append("working tree has local changes")
    if analysis.repository_hints:
        score += min(0.03 * len(analysis.repository_hints), 0.06)
        indicators.extend(analysis.repository_hints)
    score = min(score, 1.0)
    level = RiskLevel.HIGH if score >= 0.75 else RiskLevel.MEDIUM if score > 0.35 else RiskLevel.LOW
    return RiskAssessment(score=round(score, 2), level=level, indicators=indicators)


class DecisionEngine:
    """Route task requests using explicit policy and deterministic evidence."""

    def decide(
        self,
        task: TaskRequest,
        repository: RepositoryState,
        mode: UserMode = UserMode.AUTO,
        policy: RoutingPolicy | None = None,
    ) -> RoutingDecision:
        active_policy = policy or RoutingPolicy()
        analysis = analyze_task(task.description, repository)
        return self.decide_from_analysis(analysis, repository, mode, active_policy)

    def decide_from_analysis(
        self,
        analysis: TaskAnalysis,
        repository: RepositoryState,
        mode: UserMode = UserMode.AUTO,
        policy: RoutingPolicy | None = None,
    ) -> RoutingDecision:
        """Decide from already collected deterministic analysis.

        This keeps provider consultation from re-running task analysis merely to
        construct its controlled classification context.
        """
        active_policy = policy or RoutingPolicy()
        risk = assess_risk(analysis, repository)
        if mode != UserMode.AUTO:
            return self._decision(
                Route(mode.value),
                analysis,
                risk,
                0.99,
                ["explicit user mode override"],
                ["user-mode"],
            )

        hard_matches = set(analysis.categories).intersection(active_policy.hard_codex_categories)
        if hard_matches:
            return self._decision(
                Route.CODEX,
                analysis,
                risk,
                0.97,
                ["hard-risk category requires Codex"],
                ["hard-risk-category"],
            )
        if TaskCategory.DEPLOYMENT in analysis.categories:
            return self._decision(
                Route.CODEX,
                analysis,
                risk,
                0.96,
                ["production deployment requires Codex"],
                ["production-deployment"],
            )
        if analysis.possible_l0:
            return self._decision(
                Route.L0,
                analysis,
                risk,
                0.94,
                ["task matches an unambiguous deterministic formatter or lint rule"],
                ["deterministic-l0-candidate"],
            )
        if risk.score >= active_policy.direct_codex_risk:
            return self._decision(
                Route.CODEX,
                analysis,
                risk,
                0.86,
                ["risk exceeds configured direct-Codex threshold"],
                ["direct-codex-risk-threshold"],
            )
        if risk.score <= active_policy.max_gemini_risk and analysis.complexity == Complexity.LOW:
            return self._decision(
                Route.GEMINI,
                analysis,
                risk,
                0.91,
                ["localized low-risk task under configured Gemini threshold"],
                ["low-risk-gemini"],
            )
        if analysis.complexity in {Complexity.MEDIUM, Complexity.UNKNOWN}:
            return self._decision(
                Route.GEMINI_TO_CODEX,
                analysis,
                risk,
                0.72,
                ["task needs investigation or has unresolved uncertainty"],
                ["medium-uncertainty-escalation"],
            )
        return self._decision(
            Route.CODEX,
            analysis,
            risk,
            0.65,
            ["conservative fallback for non-low-risk task"],
            ["conservative-fallback"],
        )

    @staticmethod
    def _decision(
        route: Route,
        analysis: TaskAnalysis,
        risk: RiskAssessment,
        confidence: float,
        reasons: list[str],
        matched_rules: list[str],
    ) -> RoutingDecision:
        return RoutingDecision(
            route=route,
            risk=risk,
            complexity=analysis.complexity,
            scope=analysis.scope,
            confidence=confidence,
            reasons=reasons,
            matched_rules=matched_rules,
            categories=analysis.categories,
        )
