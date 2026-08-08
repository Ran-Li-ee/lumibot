import hashlib
from datetime import date as date_type
from datetime import datetime
from typing import Any

from lumibot.components.agents.schemas import BoundTool, ToolDefinition
from lumibot.example_strategies.ai_trading_team_growth_execution_test import (
    AITradingTeamGrowthExecutionTestStrategy,
)

REGIMES = (
    "growth_up_inflation_down",
    "growth_up_inflation_up",
    "growth_down_inflation_up",
    "growth_down_inflation_down",
)

BASKET_UNIVERSES = {
    "equity": ["SPY", "QQQ", "IWM", "EEM", "FXI"],
    "commodity": ["GLD", "SLV", "DBC", "PDBC", "GSG"],
    "tips": ["TIP", "SCHP", "VTIP", "STIP", "LTPZ"],
    "nominal_bond": ["SHY", "IEF", "TLT", "GOVT", "VGIT"],
}

MOCK_WEIGHT_BY_REGIME = {
    "growth_up_inflation_down": {
        "equity": 0.50,
        "commodity": 0.00,
        "tips": 0.25,
        "nominal_bond": 0.25,
    },
    "growth_up_inflation_up": {
        "equity": 0.50,
        "commodity": 0.25,
        "tips": 0.25,
        "nominal_bond": 0.00,
    },
    "growth_down_inflation_up": {
        "equity": 0.00,
        "commodity": 0.50,
        "tips": 0.25,
        "nominal_bond": 0.25,
    },
    "growth_down_inflation_down": {
        "equity": 0.25,
        "commodity": 0.00,
        "tips": 0.25,
        "nominal_bond": 0.50,
    },
}


def _parse_iso_date(value: str) -> date_type:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"date must use YYYY-MM-DD format, got {value!r}") from exc


def _seeded_regime_index(date: str, seed: int) -> int:
    digest = hashlib.sha256(f"{date}|{seed}".encode()).hexdigest()
    return int(digest[:12], 16) % len(REGIMES)


def _cycle_regime_index(date: str, seed: int) -> int:
    parsed = _parse_iso_date(date)
    return (parsed.toordinal() + int(seed)) % len(REGIMES)


def mock_macro_regime_classifier(
    date: str,
    seed: int = 42,
    mode: str = "seeded_random",
    previous_regime: str | None = None,
) -> dict[str, Any]:
    """Return a reproducible fake Growth / Inflation quadrant for workflow tests."""

    mode = str(mode).strip().lower()
    seed = int(seed)
    if mode == "seeded_random":
        index = _seeded_regime_index(date, seed)
    elif mode == "cycle":
        index = _cycle_regime_index(date, seed)
    else:
        raise ValueError("mock macro regime mode must be 'seeded_random' or 'cycle'.")

    regime = REGIMES[index]
    growth_direction, inflation_direction = regime.split("_inflation_")
    growth_direction = growth_direction.removeprefix("growth_")
    weights = dict(MOCK_WEIGHT_BY_REGIME[regime])
    return {
        "tool": "macro_regime_classifier",
        "mock": True,
        "mode": mode,
        "seed": seed,
        "date": date,
        "regime": regime,
        "growth_direction": growth_direction,
        "inflation_direction": inflation_direction,
        "previous_regime": previous_regime,
        "regime_changed": previous_regime is not None and previous_regime != regime,
        "basket_weights": weights,
        "reason_brief": (
            "Mock classifier selected this regime from deterministic date, seed, and mode logic. "
            "This is not real macro evidence."
        ),
    }


def make_macro_regime_classifier_tool() -> ToolDefinition:
    name = "macro_regime_classifier"
    description = (
        "Return a deterministic mock Growth / Inflation quadrant and basket weights for workflow testing. "
        "This tool does not perform real macro analysis."
    )
    metadata = {"kind": "mock_macro", "mock": True}

    def binder(strategy: Any, manager: Any) -> BoundTool:
        def macro_regime_classifier(
            *,
            date: str | None = None,
            seed: int | None = None,
            mode: str | None = None,
        ) -> dict[str, Any]:
            resolved_date = date or strategy.get_datetime().date().isoformat()
            resolved_seed = int(seed if seed is not None else getattr(strategy, "_mock_regime_seed", 42))
            resolved_mode = mode or getattr(strategy, "_mock_regime_mode", "seeded_random")
            previous_regime = getattr(strategy, "_last_mock_regime", None)
            result = mock_macro_regime_classifier(
                date=resolved_date,
                seed=resolved_seed,
                mode=resolved_mode,
                previous_regime=previous_regime,
            )
            strategy._last_mock_regime = result["regime"]
            return result

        return BoundTool(
            name=name,
            description=description,
            function=macro_regime_classifier,
            source="local",
            metadata=metadata,
        )

    return ToolDefinition(name=name, description=description, binder=binder, metadata=metadata)


class AITradingTeamMockGrowthInflationQuadrantStrategy(AITradingTeamGrowthExecutionTestStrategy):
    parameters = dict(AITradingTeamGrowthExecutionTestStrategy.parameters)
