"""Read-only optimization status commands."""

from __future__ import annotations

import asyncio

import typer
from rich.table import Table

from machina.cli.output import console
from machina.core.config import config
from machina.core.optimization.capabilities import resolve_capabilities
from machina.core.optimization.policy import config_summary
from machina.core.optimization.shadow import promotion_report
from machina.core.optimization.warmers.prefix import plan_prefix_warm
from machina.core.optimization.warmers.reuse import estimate_expected_turns
from machina.db.connection import db

app = typer.Typer(help="Inspect gated optimization/caching configuration.")


def _print_summary() -> None:
    summary = config_summary()
    table = Table(title="Optimization modes")
    table.add_column("Subsystem")
    table.add_column("Mode")
    for key, value in summary.items():
        table.add_row(key, str(value))
    console.print(table)


async def _report(days: int) -> dict:
    opened = False
    if not db.connected:
        await db.connect()
        opened = True
    try:
        return await promotion_report(days=days, database=db)
    finally:
        if opened:
            await db.close()


@app.command("status")
def status(
    report: bool = typer.Option(False, "--report", help="Include shadow-gate aggregates."),
    days: int = typer.Option(7, "--days", min=1, max=365, help="Report window in days."),
) -> None:
    """Show configured modes and, optionally, shadow decision counts."""
    _print_summary()
    if not report:
        return
    try:
        data = asyncio.run(_report(days))
    except Exception as exc:
        console.print(f"[red]Could not read optimization telemetry: {exc}[/red]")
        raise typer.Exit(1) from exc
    if not data:
        console.print("No shadow telemetry recorded in the selected window.")
        return
    table = Table(title=f"Optimization shadow report ({days}d)")
    table.add_column("Subsystem")
    table.add_column("Mode")
    table.add_column("Decision")
    table.add_column("Events", justify="right")
    table.add_column("Tokens before", justify="right")
    table.add_column("Tokens after", justify="right")
    table.add_column("Avg similarity", justify="right")
    table.add_column("Avg latency ms", justify="right")
    for subsystem, bucket in sorted(data.items()):
        for decision, values in sorted(bucket.get("decisions", {}).items()):
            table.add_row(
                subsystem,
                str(bucket.get("mode", "")),
                decision,
                str(values["events"]),
                str(values["tokensBefore"]),
                str(values["tokensAfter"]),
                f"{values['avgSimilarity']:.3f}" if values["avgSimilarity"] else "-",
                f"{values['avgLatencyMs']:.1f}" if values["avgLatencyMs"] else "-",
            )
    console.print(table)


@app.command("prefix-plan")
def prefix_plan(
    provider: str = typer.Argument(..., help="Provider name (e.g. anthropic)."),
    model: str = typer.Option(None, "--model", help="Selected model id."),
    prefix_tokens: int = typer.Option(4096, "--prefix-tokens", min=0),
    min_prefix_tokens: int | None = typer.Option(
        None, "--min-prefix-tokens", min=0, help="Override the configured minimum."
    ),
    min_expected_turns: float = typer.Option(
        2.0, "--min-expected-turns", min=0.0, help="Minimum reuse before a plan is positive."
    ),
    max_budget_usd: float = typer.Option(
        0.5, "--max-budget-usd", min=0.0, help="Maximum net spend for this plan."
    ),
    expected_turns: float | None = typer.Option(
        None, "--expected-turns", help="Override the reuse estimate."
    ),
    history_turns: str = typer.Option(
        "", "--history-turns", help="Comma-separated prior session turn counts."
    ),
) -> None:
    """Estimate paid prefix-warming economics without making a provider call."""
    history: list[int] = []
    for item in (history_turns or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            history.append(int(item))
        except ValueError:
            raise typer.BadParameter("--history-turns must be comma-separated integers") from None
    expected = (
        float(expected_turns)
        if expected_turns is not None
        else estimate_expected_turns(history).expected_turns
    )
    profile = resolve_capabilities(provider, model)
    plan = plan_prefix_warm(
        profile,
        prefix_tokens=prefix_tokens,
        expected_turns=expected,
        min_prefix_tokens=(
            int(config.get("optimization_warmers_min_prefix_tokens"))
            if min_prefix_tokens is None
            else min_prefix_tokens
        ),
        min_expected_turns=min_expected_turns,
        max_budget_usd=max_budget_usd,
    )
    table = Table(title="Prefix-warming estimate")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("provider/model", f"{profile.provider}/{profile.model or '?'}")
    table.add_row("prompt-cache mode", profile.prompt_cache.mode)
    table.add_row("context window", str(profile.context_window))
    table.add_row(
        "cache TTL seconds",
        str(profile.cache_ttl_seconds) if profile.cache_ttl_seconds is not None else "unknown",
    )
    table.add_row("verified complete", str(profile.verified_complete))
    table.add_row("expected turns", f"{expected:.2f}")
    table.add_row("allowed", str(plan.allowed))
    table.add_row("reason", plan.reason)
    table.add_row(
        "estimated net USD",
        f"{plan.estimated_savings_usd:.6f}"
        if plan.estimated_savings_usd is not None
        else "unknown",
    )
    console.print(table)
