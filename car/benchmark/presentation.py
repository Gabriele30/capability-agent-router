"""Rich rendering for aggregate benchmark reports."""

from rich.console import Console
from rich.table import Table

from car.benchmark.aggregation import BenchmarkReport


def render_benchmark_report(report: BenchmarkReport, console: Console) -> None:
    console.print("[bold]CAR Economic Benchmark[/]")
    table = Table(show_header=True)
    for column in (
        "Strategy",
        "N",
        "Success",
        "Rate",
        "Ref cost",
        "$/success",
        "Avg latency",
        "Unknown",
    ):
        table.add_column(column)
    for item in report.summaries:
        table.add_row(
            item.strategy.value.replace("_", "-"),
            str(item.task_count),
            str(item.verified_success_count),
            f"{item.success_rate * 100:.1f}%",
            _money(item.total_reference_cost_usd),
            _money(item.cost_per_verified_success_usd),
            f"{item.mean_latency_ms:.0f} ms",
            str(item.unknown_cost_count),
        )
    console.print(table)
    if report.comparison:
        comparison = report.comparison
        console.print("\n[bold]CAR vs Codex-only[/]")
        console.print(
            f"Success delta: {_percent(comparison.success_delta_percentage_points, 'pp')}"
        )
        console.print(f"Cost delta: {_percent(comparison.total_reference_cost_delta_percent)}")
        console.print(
            "Cost/success delta: " + _percent(comparison.cost_per_verified_success_delta_percent)
        )
        console.print(f"Mean latency delta: {_percent(comparison.mean_latency_delta_percent)}")
        car = next(item for item in report.summaries if item.strategy.value == "car")
        console.print(f"Codex escalation: {car.codex_escalation_count}/{car.task_count}")
        console.print(f"Codex avoidance: {car.codex_avoidance_count}/{car.task_count}")
    console.print("\nReference pricing: public API list-price snapshot verified 2026-08-11.")
    console.print(
        "N/A = incomplete usage or pricing data. Reference cost != actual provider billing."
    )


def _money(value: float | None) -> str:
    return "N/A" if value is None else f"${value:.6f}"


def _percent(value: float | None, suffix: str = "%") -> str:
    return "N/A" if value is None else f"{value:+.1f} {suffix}"
