"""Skill discovery, enrichment, curation and hub commands for the Machina CLI."""

from __future__ import annotations

import typer
from rich.markup import escape
from rich.table import Table

from machina import services
from machina.cli import output
from machina.cli.output import console
from machina.observability.diagnostics import record_failure
from machina.skills import enrich
from machina.skills.registry import log_skill_metadata_event

skills_app = typer.Typer(
    name="skills",
    help="Inspect skills and backfill missing schema-v2 metadata.",
    invoke_without_command=True,
)


@skills_app.callback()
def skills(ctx: typer.Context):
    """List all loaded skills (default when no subcommand is given)."""
    if ctx.invoked_subcommand is None:
        skills_list()


def skills_list():
    """List all loaded skills."""
    from machina.skills.registry import skill_registry

    skill_registry.load_all()
    all_skills = skill_registry.list_skills()
    if not all_skills:
        console.print("[yellow]No skills found.[/yellow]")
        return
    output.skills_table(all_skills)
    # Fail-closed skills stay visible to operators, but never work silently:
    # report them and point at metadata generation.
    unusable = [skill for skill in all_skills if skill.skill_metadata is None]
    if unusable:
        console.print(
            f"[red]{len(unusable)} skill(s) unusable (no valid schema-v2 metadata):[/red] "
            + ", ".join(escape(skill.name) for skill in unusable[:20])
            + (" …" if len(unusable) > 20 else "")
        )
        console.print(
            "[yellow]Run metadata generation (`machina skills enrich <name>` or "
            "`machina skills enrich --all`) to make them usable.[/yellow]"
        )


def learn(
    source: str = typer.Argument(..., help="Source to learn from (file path, URL, or skill name)"),
    name: str | None = typer.Option(None, "--name", "-n", help="Name for the new skill"),
    description: str | None = typer.Option(
        None, "--description", "-d", help="Description for the new skill"
    ),
    triggers: str | None = typer.Option(
        None, "--triggers", "-t", help="Comma-separated trigger words"
    ),
):
    """Learn a new skill from a source (file, URL, or existing skill)."""
    trigger_list = [t.strip() for t in triggers.split(",") if t.strip()] if triggers else None
    try:
        skill = services.learn_skill(
            source, name=name, description=description, triggers=trigger_list
        )
    except services.ServiceError as e:
        output.error(str(e))
        raise typer.Exit(1) from None
    generation_error = ""
    try:
        enrich.ensure_skill_metadata_sync(skill, agent_seam=enrich.default_agent_seam())
    except Exception as exc:  # reported below as a failed learn, never silent
        generation_error = type(exc).__name__
        record_failure("cli.learn.metadata", exc)
    if skill.skill_metadata is None:
        log_skill_metadata_event(
            skill.name,
            skill.file_path,
            status="failed",
            error_type=generation_error or "SkillMetadataUnavailable",
        )
        output.error(
            f"Skill '{skill.name}' was written to {skill.file_path} but is UNUSABLE: "
            "metadata generation failed "
            f"({generation_error or 'no schema-v2 metadata produced'}). "
            f"Retry with `machina skills enrich {skill.name}`."
        )
        raise typer.Exit(1)
    log_skill_metadata_event(skill.name, skill.file_path, status="generated")
    console.print(f"[green]Skill '{escape(skill.name)}' created successfully![/green]")
    console.print(f"  Description: {escape(skill.description)}")
    console.print(f"  Triggers: {escape(', '.join(skill.triggers))}")
    console.print(f"  Source: {escape(skill.source)}")


@skills_app.command("enrich")
def skills_enrich(
    name: str | None = typer.Argument(None, help="Skill to enrich (omit with --all)"),
    all_: bool = typer.Option(
        False, "--all", help="Backfill metadata for every skill that lacks it"
    ),
):
    """Generate missing schema-v2 metadata for installed/local skills.

    This is the explicit operator retry path for a failed install/learn. It is
    idempotent (valid metadata is never regenerated) and never runs at query
    time. Exits nonzero when any requested skill remains unusable.
    """
    from machina.skills.registry import skill_registry

    if not all_ and not name:
        output.error("Provide a skill NAME or --all.")
        raise typer.Exit(1)
    skill_registry.load_all(refresh=True)
    if all_:
        # Bundled ECC content is package data: metadata lives in its revision-
        # keyed sidecar, not in a writable SKILL.md.
        targets = [
            skill
            for skill in sorted(skill_registry.list_skills(), key=lambda item: item.name)
            if skill.source_type != "builtin-ecc"
        ]
    else:
        target = skill_registry.get(name)
        if target is None:
            output.error(f"Skill '{name}' not found.")
            raise typer.Exit(1)
        if target.source_type == "builtin-ecc":
            output.error(
                f"Skill '{name}' is bundled ECC content; its metadata comes from the "
                "revision-keyed enrichment sidecar and cannot be backfilled here."
            )
            raise typer.Exit(1)
        targets = [target]

    generated = 0
    skipped = 0
    failed: list[str] = []
    for skill in targets:
        if skill.skill_metadata is not None:
            skipped += 1
            console.print(f"  [dim]{escape(skill.name)}: already usable (skipped)[/dim]")
            log_skill_metadata_event(skill.name, skill.file_path, status="present")
            continue
        error_type = ""
        try:
            enrich.ensure_skill_metadata_sync(
                skill, agent_seam=enrich.default_agent_seam(), registry=skill_registry
            )
        except Exception as exc:  # reported per skill below
            error_type = type(exc).__name__
            record_failure("cli.skills.enrich", exc)
        if skill.skill_metadata is not None:
            generated += 1
            console.print(f"  [green]{escape(skill.name)}: metadata generated[/green]")
            log_skill_metadata_event(skill.name, skill.file_path, status="generated")
        else:
            failed.append(skill.name)
            reason = skill.metadata_error or "no schema-v2 metadata produced"
            console.print(
                f"  [red]{escape(skill.name)}: FAILED — still UNUSABLE "
                f"({escape(error_type or reason)})[/red]"
            )
            log_skill_metadata_event(
                skill.name,
                skill.file_path,
                status="failed",
                error_type=error_type or "SkillMetadataUnavailable",
            )
    console.print(
        f"[bold]Metadata enrichment:[/bold] {generated} generated, "
        f"{skipped} already usable, {len(failed)} failed."
    )
    if failed:
        console.print(
            "[red]Still unusable: "
            + ", ".join(escape(item) for item in failed)
            + "[/red] — check provider configuration and re-run `machina skills enrich`."
        )
        raise typer.Exit(1)


def curator(
    action: str = typer.Argument(
        "report", help="Action: report, archive, cleanup, stale, unused, top"
    ),
    days: int = typer.Option(30, "--days", "-d", help="Days threshold for stale skills"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of results to show"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be done without doing it"
    ),
):
    """Run skill curator — background maintenance for skills.

    Actions:
        report   — Show skill system health report
        archive  — Archive stale skills (not used in N days)
        cleanup  — Remove unused skills (never used)
        stale    — List stale skills
        unused   — List never-used skills
        top      — Show most frequently used skills
    """
    from machina.skills.registry import SkillCurator, skill_registry

    skill_registry.load_all()
    curator = SkillCurator(skill_registry)

    if action == "report":
        report = curator.get_health_report()
        console.print("[bold]Skill System Health Report[/bold]\n")
        console.print(f"  Total skills: {report['total_skills']}")
        console.print(f"  Enabled: {report['enabled']}")
        console.print(f"  Disabled: {report['disabled']}")
        console.print(f"  Never used: {report['never_used']}")
        console.print(f"  Total uses: {report['total_uses']}")
        console.print(f"  Total views: {report['total_views']}")
        console.print(f"  Stale skills: {report['stale_skills']}")
        if report["top_skills"]:
            console.print("\n[bold]Top skills:[/bold]")
            for s in report["top_skills"]:
                console.print(f"  {escape(s['name'])}: {s['usage_count']} uses")

    elif action == "archive":
        archived = curator.archive_stale(days=days)
        if archived:
            console.print(f"[green]Archived {len(archived)} stale skills:[/green]")
            for name in archived:
                console.print(f"  - {escape(name)}")
        else:
            console.print("[yellow]No stale skills found.[/yellow]")

    elif action == "cleanup":
        removed = curator.cleanup_unused(dry_run=dry_run)
        if removed:
            prefix = "[yellow]Would remove[/yellow]" if dry_run else "[green]Removed[/green]"
            console.print(f"{prefix} {len(removed)} unused skills:")
            for name in removed:
                console.print(f"  - {escape(name)}")
        else:
            console.print("[yellow]No unused skills found.[/yellow]")

    elif action == "stale":
        stale = curator.get_stale_skills(days=days)
        if stale:
            console.print(f"[bold]Stale skills ({len(stale)}):[/bold]")
            for s in stale:
                last = s.last_activity_at.strftime("%Y-%m-%d") if s.last_activity_at else "never"
                console.print(f"  {escape(s.name)} (last activity: {last})")
        else:
            console.print("[yellow]No stale skills found.[/yellow]")

    elif action == "unused":
        unused = curator.get_unused_skills()
        if unused:
            console.print(f"[bold]Unused skills ({len(unused)}):[/bold]")
            for s in unused:
                console.print(f"  {escape(s.name)} (created: {s.created_at.strftime('%Y-%m-%d')})")
        else:
            console.print("[yellow]No unused skills found.[/yellow]")

    elif action == "top":
        top = curator.get_top_skills(limit=limit)
        if top:
            console.print(f"[bold]Top {len(top)} skills:[/bold]")
            for i, s in enumerate(top, 1):
                console.print(
                    f"  {i}. {escape(s.name)} — {s.usage_count} uses, {s.view_count} views"
                )
        else:
            console.print("[yellow]No skill usage data.[/yellow]")

    else:
        output.error(f"Unknown action: {action}")
        console.print("[dim]Available: report, archive, cleanup, stale, unused, top[/dim]")
        raise typer.Exit(1)


def hub(
    action: str = typer.Argument("list", help="Action: list, search, publish, install, telemetry"),
    query: str | None = typer.Option(None, "--query", "-q", help="Search query"),
    name: str | None = typer.Option(None, "--name", "-n", help="Skill name"),
    author: str | None = typer.Option(None, "--author", "-a", help="Author name for publish"),
    tags: str | None = typer.Option(None, "--tags", "-t", help="Comma-separated tags for publish"),
):
    """Skill hub — community-curated skill sharing.

    Actions:
        list      — List all skills in the hub
        search    — Search hub skills
        publish   — Publish a local skill to the hub
        install   — Install a skill from the hub
        telemetry — Show hub skill telemetry
    """
    from machina.skills.registry import SkillHub, skill_registry

    skill_registry.load_all()
    hub = SkillHub(skill_registry)

    if action == "list":
        skills = hub.list_hub()
        if not skills:
            console.print("[yellow]No skills in hub.[/yellow]")
            return
        table = Table(title=f"Skill Hub ({len(skills)} skills)")
        table.add_column("Name", style="cyan")
        table.add_column("Description", style="white")
        table.add_column("Author", style="dim")
        table.add_column("Tags", style="dim")
        table.add_column("Version", style="dim")
        for s in skills:
            table.add_row(
                escape(s.get("name", "")),
                escape(s.get("description", "")[:60]),
                escape(s.get("author", "")),
                escape(", ".join(s.get("tags", [])[:3])),
                escape(s.get("version", "1.0.0")),
            )
        console.print(table)

    elif action == "search":
        if not query:
            console.print("[red]Query required for search.[/red]")
            raise typer.Exit(1)
        results = hub.search(query)
        if not results:
            console.print(f"[yellow]No skills found for '{escape(query)}'[/yellow]")
            return
        console.print(f"[bold]Search results for '{escape(query)}' ({len(results)}):[/bold]")
        for s in results:
            console.print(
                f"\n  [cyan]{escape(str(s.get('name', '')))}[/cyan] — {escape(str(s.get('description', ''))[:80])}"
            )
            console.print(
                f"    Author: {escape(str(s.get('author', 'unknown')))} | Tags: {escape(', '.join(str(t) for t in s.get('tags', [])))}"
            )

    elif action == "publish":
        if not name:
            console.print("[red]Skill name required for publish.[/red]")
            raise typer.Exit(1)
        tag_list = [t.strip() for t in tags.split(",")] if tags else []
        try:
            entry = hub.publish(name, author=author or "", tags=tag_list)
            console.print(f"[green]Published '{escape(entry['name'])}' to hub.[/green]")
        except ValueError as e:
            output.error(str(e))
            raise typer.Exit(1) from None

    elif action == "install":
        if not name:
            console.print("[red]Skill name required for install.[/red]")
            raise typer.Exit(1)
        try:
            skill = hub.install(name)
            console.print(f"[green]Installed '{escape(skill.name)}' from hub.[/green]")
            console.print(f"  Description: {escape(skill.description)}")
            console.print(f"  Triggers: {escape(', '.join(skill.triggers))}")
        except ValueError as e:
            output.error(str(e))
            raise typer.Exit(1) from None

    elif action == "telemetry":
        telemetry = hub.get_hub_telemetry()
        if not telemetry:
            console.print("[yellow]No hub telemetry available.[/yellow]")
            return
        table = Table(title="Hub Telemetry")
        table.add_column("Name", style="cyan")
        table.add_column("Uses", style="yellow")
        table.add_column("Views", style="green")
        table.add_column("Published", style="dim")
        for t in telemetry:
            table.add_row(
                escape(t.get("name", "")),
                escape(str(t.get("usage_count", 0))),
                escape(str(t.get("view_count", 0))),
                escape(t.get("published_at", "")[:10]),
            )
        console.print(table)

    else:
        output.error(f"Unknown action: {action}")
        console.print("[dim]Available: list, search, publish, install, telemetry[/dim]")
        raise typer.Exit(1)
