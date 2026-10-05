"""Command-line interface for repo-planner."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__, report
from .config import Project, detect_git_origin, load_project, load_token, scaffold_project
from .console import Console
from .errors import ConfigurationError, RepoPlannerError
from .importer import import_existing
from .manifest import load_manifest, validate_documents
from .models import Manifest, ProjectState
from .providers import create_provider
from .providers.base import Provider
from .reconciler import Reconciler
from .state import load_state, save_state, state_scope_warning
from .sync import sync_milestones, write_cache
from .templates import TemplateRenderer

KIND_CHOICES = {"all": {"milestone", "issue"}, "milestones": {"milestone"}, "issues": {"issue"}}


# --------------------------------------------------------------------- helpers


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        metavar="DIR",
        default=argparse.SUPPRESS,
        help="project root directory (default: current directory)",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="path to the project configuration file (default: <root>/configs/repo-planner.json)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="show detailed output, including unchanged resources",
    )


def _add_mutation_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="preview what would happen without writing anything",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=argparse.SUPPRESS,
        help="apply the changes (required for publish)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        default=argparse.SUPPRESS,
        help="assume yes for confirmation prompts (required in non-interactive sessions)",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=argparse.SUPPRESS,
        metavar="KEY",
        help="limit the operation to KEY (repeatable, commas accepted)",
    )
    parser.add_argument(
        "--force-update",
        action="store_true",
        default=argparse.SUPPRESS,
        help="overwrite remote resources that changed since the last recorded run",
    )


def _project(args: argparse.Namespace) -> Project:
    root = getattr(args, "root", None)
    config = getattr(args, "config", None)
    return load_project(
        root=Path(root) if root else None,
        config_path=Path(config) if config else None,
    )


def _parse_only(args: argparse.Namespace) -> set[str] | None:
    raw = getattr(args, "only", None)
    if not raw:
        return None
    keys: set[str] = set()
    for item in raw:
        for part in str(item).split(","):
            part = part.strip()
            if part:
                keys.add(part)
    return keys or None


def _ensure_known_keys(manifest: Manifest, only: set[str] | None) -> None:
    if not only:
        return
    known = {spec.key for spec in manifest.milestones} | {spec.key for spec in manifest.issues}
    unknown = sorted(only - known)
    if unknown:
        raise ConfigurationError("unknown key(s) passed to --only: " + ", ".join(unknown))


def _load_context(
    args: argparse.Namespace,
    console: Console,
) -> tuple[Project, Manifest, ProjectState, TemplateRenderer, Provider]:
    project = _project(args)
    state = load_state(project.state_path)
    warning = state_scope_warning(
        state,
        provider=project.config.provider,
        repository=project.config.repository,
    )
    if warning:
        console.warn(warning)
    manifest = load_manifest(project.manifest_path, root=project.root)
    token = load_token(project.config)
    provider = create_provider(project.config, token=token)
    return project, manifest, state, TemplateRenderer(), provider


def _make_reconciler(
    project: Project,
    manifest: Manifest,
    state: ProjectState,
    renderer: TemplateRenderer,
    provider: Provider,
) -> Reconciler:
    return Reconciler(
        root=project.root,
        config=project.config,
        manifest=manifest,
        state=state,
        provider=provider,
        renderer=renderer,
        state_path=project.state_path,
    )


def _resolve_apply(args: argparse.Namespace) -> bool:
    dry_run = bool(getattr(args, "dry_run", False))
    apply_flag = bool(getattr(args, "apply", False))
    if dry_run and apply_flag:
        raise ConfigurationError("--dry-run and --apply are mutually exclusive")
    return apply_flag and not dry_run


# -------------------------------------------------------------------- commands


def cmd_init(args: argparse.Namespace, console: Console) -> int:
    root = Path(getattr(args, "root", None) or ".").expanduser()
    detected = detect_git_origin(root)
    provider = getattr(args, "provider", None) or (detected[0] if detected else None)
    repository = getattr(args, "repository", None) or (detected[1] if detected else None)
    if repository is None:
        raise ConfigurationError(
            "no repository specified and no git remote detected; pass --repository owner/name"
        )
    provider = provider or "github"
    dry_run = bool(getattr(args, "dry_run", False))
    results = scaffold_project(
        root,
        provider=provider,
        repository=repository,
        force=bool(getattr(args, "force", False)),
        dry_run=dry_run,
    )
    for path, status in results:
        console.print(f"{status}: {path}")
    if dry_run:
        console.print("dry run: no files were written.")
    else:
        console.print("next steps: review configs/manifest.json, then run 'repo-planner validate'")
    return 0


def cmd_validate(args: argparse.Namespace, console: Console) -> int:
    project = _project(args)
    manifest = load_manifest(project.manifest_path, root=project.root)
    problems = validate_documents(project.root, project.config, manifest, TemplateRenderer())
    if problems:
        console.error("validation failed:")
        for problem in problems:
            console.error(f"  - {problem}")
        return 1
    console.print(
        f"manifest ok: {len(manifest.milestones)} milestone(s), {len(manifest.issues)} issue(s)"
    )
    return 0


def cmd_plan(args: argparse.Namespace, console: Console) -> int:
    project, manifest, state, renderer, provider = _load_context(args, console)
    only = _parse_only(args)
    _ensure_known_keys(manifest, only)
    reconciler = _make_reconciler(project, manifest, state, renderer, provider)
    plan = reconciler.plan(only=only)
    for line in report.render_plan(plan, verbose=console.verbose):
        console.print(line)
    if plan.conflicts():
        console.print(
            f"{len(plan.conflicts())} resource(s) changed remotely; "
            "publishing them requires --force-update"
        )
    blocking = [
        problem for problem in reconciler.preflight_problems(plan) if problem.kind != "conflict"
    ]
    if blocking:
        for problem in blocking:
            console.error(f"error: {problem.message}")
        return 1
    return 0


def cmd_sync(args: argparse.Namespace, console: Console) -> int:
    project, manifest, _state, _renderer, provider = _load_context(args, console)
    only = _parse_only(args)
    _ensure_known_keys(manifest, only)
    dry_run = bool(getattr(args, "dry_run", False))
    _resolve_apply(args)  # validates flag combinations
    cache = sync_milestones(project=project, provider=provider, only=only)
    if dry_run:
        console.print(
            f"dry run: would cache {len(cache.milestones)} milestone(s) to {project.cache_path}"
        )
        return 0
    path = write_cache(project, cache)
    console.print(f"cached {len(cache.milestones)} milestone(s) to {path}")
    return 0


def cmd_publish(args: argparse.Namespace, console: Console) -> int:
    mode = args.resource
    kinds = KIND_CHOICES[mode]
    apply = _resolve_apply(args)
    assume_yes = bool(getattr(args, "yes", False))
    create_missing_labels = bool(getattr(args, "create_missing_labels", False))
    force_update = bool(getattr(args, "force_update", False))

    project, manifest, state, renderer, provider = _load_context(args, console)
    only = _parse_only(args)
    _ensure_known_keys(manifest, only)
    reconciler = _make_reconciler(project, manifest, state, renderer, provider)
    plan = reconciler.plan(only=only, kinds=None if kinds == {"milestone", "issue"} else kinds)

    for line in report.render_plan(plan, verbose=console.verbose):
        console.print(line)

    problems = reconciler.preflight_problems(
        plan, create_missing_labels=create_missing_labels, force_update=force_update
    )
    if not apply:
        console.print(
            "dry run: no changes were applied. pass --apply to mutate the remote repository."
        )
        for problem in problems:
            console.error(f"error: {problem.message}")
        return 1 if problems else 0

    reconciler.ensure_ready(
        plan, create_missing_labels=create_missing_labels, force_update=force_update
    )
    changes = [resource for resource in plan.resources if resource.action != "skip"]
    if changes and not assume_yes:
        if not sys.stdin.isatty():
            raise ConfigurationError(
                "refusing to mutate without confirmation in a non-interactive session; pass --yes"
            )
        answer = input(f"apply {len(changes)} change(s)? [y/N] ")
        if answer.strip().casefold() not in {"y", "yes"}:
            console.print("aborted: no changes applied.")
            return 0

    summary = reconciler.execute(
        plan, create_missing_labels=create_missing_labels, force_update=force_update
    )
    for message in summary.messages:
        console.print(message)
    console.print(
        f"done: {summary.created_milestones + summary.created_issues} created, "
        f"{summary.updated_milestones + summary.updated_issues} updated, "
        f"{summary.skipped} unchanged"
    )
    return 0


def cmd_import(args: argparse.Namespace, console: Console) -> int:
    project, manifest, state, _renderer, provider = _load_context(args, console)
    only = _parse_only(args)
    _ensure_known_keys(manifest, only)
    dry_run = bool(getattr(args, "dry_run", False))
    _resolve_apply(args)  # validates flag combinations
    assume_yes = bool(getattr(args, "yes", False))
    kinds = KIND_CHOICES[getattr(args, "kind", "all")]

    prompt = input if (not assume_yes and sys.stdin.isatty()) else None
    outcome = import_existing(
        manifest=manifest,
        state=state,
        provider=provider,
        kinds=kinds,
        only=only,
        dry_run=dry_run,
        prompt=prompt,
        console=console,
    )
    for line in outcome.attached:
        console.print(line)
    if outcome.skipped:
        console.print(f"skipped {len(outcome.skipped)} resource(s)")
        for line in outcome.skipped:
            console.detail(line)
    if dry_run:
        console.print("dry run: state file not modified.")
    elif outcome.changed:
        save_state(project.state_path, state)
        console.print(f"updated {project.state_path}")
    else:
        console.print("nothing to import.")
    return 0


# ---------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-planner",
        description=(
            "Declarative management of GitHub and GitLab milestones and issues from JSON manifests."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _add_common(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser(
        "init",
        help="scaffold the configuration files in the project root",
        description=(
            "Create configs/repo-planner.json, configs/manifest.json and configs/state.json."
        ),
    )
    _add_common(init)
    _add_mutation_flags(init)
    init.add_argument(
        "--provider",
        choices=["github", "gitlab"],
        default=argparse.SUPPRESS,
        help="repository host (default: detected from the git remote, else github)",
    )
    init.add_argument(
        "--repository",
        metavar="OWNER/NAME",
        default=argparse.SUPPRESS,
        help="repository to manage (default: detected from the git remote)",
    )
    init.add_argument(
        "--force",
        action="store_true",
        default=argparse.SUPPRESS,
        help="overwrite existing configuration files",
    )
    init.set_defaults(handler=cmd_init)

    validate = subparsers.add_parser(
        "validate",
        help="validate the manifest and referenced Markdown documents (offline)",
    )
    _add_common(validate)
    validate.set_defaults(handler=cmd_validate)

    plan = subparsers.add_parser(
        "plan",
        help="print a structured diff between the manifest and the remote repository",
        description="Never mutates anything: remote resources are only read.",
    )
    _add_common(plan)
    plan.add_argument(
        "--only",
        action="append",
        default=argparse.SUPPRESS,
        metavar="KEY",
        help="limit the plan to KEY (repeatable, commas accepted)",
    )
    plan.set_defaults(handler=cmd_plan)

    sync = subparsers.add_parser(
        "sync",
        help="synchronize local caches from the remote provider",
        description="Writes a local cache only; remote resources are never modified.",
    )
    _add_common(sync)
    _add_mutation_flags(sync)
    sync.add_argument("resource", choices=["milestones"], help="resource to synchronize")
    sync.set_defaults(handler=cmd_sync)

    importer = subparsers.add_parser(
        "import-existing",
        help="attach existing remote resources to manifest keys",
        description=(
            "Resources are attached after a reliable identity-marker match or an "
            "explicit interactive selection."
        ),
    )
    _add_common(importer)
    _add_mutation_flags(importer)
    importer.add_argument(
        "--kind",
        choices=["all", "milestones", "issues"],
        default="all",
        help="which resources to import (default: all)",
    )
    importer.set_defaults(handler=cmd_import)

    publish = subparsers.add_parser(
        "publish",
        help="create or update remote resources (dry-run unless --apply)",
        description=(
            "Prints the plan, validates labels/assignees and conflicts, then applies "
            "changes only when --apply is explicitly supplied."
        ),
    )
    _add_common(publish)
    _add_mutation_flags(publish)
    publish.add_argument(
        "resource",
        choices=["milestones", "issues", "all"],
        help="which resources to publish",
    )
    publish.add_argument(
        "--create-missing-labels",
        action="store_true",
        default=argparse.SUPPRESS,
        help="create missing labels in the repository before publishing",
    )
    publish.set_defaults(handler=cmd_publish)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""

    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console(verbose=bool(getattr(args, "verbose", False)))
    try:
        return int(args.handler(args, console))
    except RepoPlannerError as exc:
        _print_error(console, exc)
        return exc.exit_code
    except KeyboardInterrupt:
        console.error("aborted")
        return 130


def _print_error(console: Console, exc: RepoPlannerError) -> None:
    lines = str(exc).splitlines() or [exc.__class__.__name__]
    console.error(f"error: {lines[0]}")
    for line in lines[1:]:
        console.error(line)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
