"""Reconciliation engine: resolve remote resources, plan and execute changes.

The engine implements the documented resolution order:

1. the remote ID recorded in ``configs/state.json``,
2. the resource identity marker found in a remote description/body,
3. exact title matching as a last resort,
4. an actionable ambiguity error when several resources match,
5. creation of the resource only when no reliable match exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import diff, markers
from .config import ProjectConfig
from .dependencies import topological_order
from .errors import AmbiguousResourceError, ManifestValidationError, RemoteConflictError
from .files import atomic_write_json
from .models import (
    IssuePayload,
    IssueSpec,
    Manifest,
    MilestonePayload,
    MilestoneSpec,
    ProjectState,
    RemoteIssue,
    RemoteMilestone,
    ResourceState,
)
from .paths import read_document
from .providers.base import Provider
from .templates import TemplateRenderer
from .views import issue_ref_dict, milestone_ref_dict, repository_dict


@dataclass
class Resolution:
    """Outcome of resolving a manifest key against the remote state."""

    remote: RemoteIssue | RemoteMilestone | None = None
    matched_by: str | None = None


@dataclass
class ResourcePlan:
    """Planned action for a single milestone or issue."""

    kind: str  # "milestone" | "issue"
    key: str
    title: str
    action: str  # "create" | "update" | "skip"
    changes: list[diff.FieldChange] = field(default_factory=list)
    conflict: bool = False
    conflict_detail: str | None = None
    matched_by: str | None = None
    remote_id: int | None = None
    remote_number: int | None = None
    remote: RemoteIssue | RemoteMilestone | None = None
    payload: IssuePayload | MilestonePayload | None = None


@dataclass
class Plan:
    """The complete plan produced by :class:`Reconciler`."""

    milestones: list[ResourcePlan] = field(default_factory=list)
    issues: list[ResourcePlan] = field(default_factory=list)
    missing_labels: list[str] = field(default_factory=list)
    missing_assignees: list[str] = field(default_factory=list)

    @property
    def resources(self) -> list[ResourcePlan]:
        return [*self.milestones, *self.issues]

    def with_action(self, action: str) -> list[ResourcePlan]:
        return [resource for resource in self.resources if resource.action == action]

    def conflicts(self) -> list[ResourcePlan]:
        return [
            resource
            for resource in self.resources
            if resource.conflict and resource.action == "update"
        ]


@dataclass(frozen=True)
class PreflightProblem:
    """A problem that prevents ``publish`` from running."""

    kind: str  # "labels" | "assignees" | "conflict"
    message: str


@dataclass
class ExecutionSummary:
    """Result of applying a plan."""

    created_milestones: int = 0
    updated_milestones: int = 0
    created_issues: int = 0
    updated_issues: int = 0
    skipped: int = 0
    created_labels: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


class Reconciler:
    """Bridges the local manifest, the local state and the remote provider."""

    def __init__(
        self,
        *,
        root: Path,
        config: ProjectConfig,
        manifest: Manifest,
        state: ProjectState,
        provider: Provider,
        renderer: TemplateRenderer,
        state_path: Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.config = config
        self.manifest = manifest
        self.state = state
        self.provider = provider
        self.renderer = renderer
        self._state_path = Path(state_path) if state_path is not None else None
        self._state_dirty = False
        self._index_milestones: list[RemoteMilestone] | None = None
        self._index_issues: list[RemoteIssue] | None = None
        self._resolved: dict[tuple[str, str], Resolution] = {}
        self._milestone_numbers: dict[str, int] = {}

    # ------------------------------------------------------------------ state

    def save_state(self) -> None:
        """Persist the state file if it changed (atomic write)."""

        if self._state_path is None or not self._state_dirty:
            return
        self.state.provider = self.config.provider
        self.state.repository = self.config.repository
        atomic_write_json(self._state_path, self.state.model_dump(mode="json"))
        self._state_dirty = False

    def _record(self, kind: str, key: str, remote: RemoteIssue | RemoteMilestone) -> None:
        entry = ResourceState(
            id=remote.id,
            number=remote.number,
            updated_at=diff.normalize_timestamp(remote.updated_at),
            url=remote.url,
        )
        bucket = self.state.bucket(kind)
        if bucket.get(key) != entry:
            bucket[key] = entry
            self._state_dirty = True

    # -------------------------------------------------------------- resolution

    def load_remote(self) -> None:
        """Fetch remote milestones and issues once per run."""

        if self._index_milestones is None:
            self._index_milestones = self.provider.list_milestones()
        if self._index_issues is None:
            self._index_issues = self.provider.list_issues()

    @property
    def milestones_index(self) -> list[RemoteMilestone]:
        self.load_remote()
        assert self._index_milestones is not None
        return self._index_milestones

    @property
    def issues_index(self) -> list[RemoteIssue]:
        self.load_remote()
        assert self._index_issues is not None
        return self._index_issues

    def resolve_milestone(self, spec: MilestoneSpec) -> Resolution:
        """Resolve a milestone specification against the remote repository."""

        cache_key = ("milestone", spec.key)
        if cache_key not in self._resolved:
            self._resolved[cache_key] = self._resolve(
                kind="milestone", key=spec.key, title=spec.title
            )
        return self._resolved[cache_key]

    def resolve_issue(self, spec: IssueSpec) -> Resolution:
        """Resolve an issue specification against the remote repository."""

        cache_key = ("issue", spec.key)
        if cache_key not in self._resolved:
            self._resolved[cache_key] = self._resolve(kind="issue", key=spec.key, title=spec.title)
        return self._resolved[cache_key]

    def _resolve(self, *, kind: str, key: str, title: str) -> Resolution:
        resources: list[Any] = self.milestones_index if kind == "milestone" else self.issues_index
        text_attr = "description" if kind == "milestone" else "body"

        entry = self.state.bucket(kind).get(key)
        if entry is not None:
            for resource in resources:
                if resource.id == entry.id:
                    return Resolution(remote=resource, matched_by="state")

        marker_matches = [
            resource
            for resource in resources
            if markers.has_marker(getattr(resource, text_attr), kind, key)
        ]
        if len(marker_matches) > 1:
            raise AmbiguousResourceError(
                self._ambiguity_message(kind, key, marker_matches, "its identity marker")
            )
        if marker_matches:
            return Resolution(remote=marker_matches[0], matched_by="marker")

        title_matches = [resource for resource in resources if resource.title == title]
        if len(title_matches) > 1:
            raise AmbiguousResourceError(
                self._ambiguity_message(kind, key, title_matches, f"the exact title {title!r}")
            )
        if title_matches:
            return Resolution(remote=title_matches[0], matched_by="title")
        return Resolution(remote=None, matched_by=None)

    @staticmethod
    def _ambiguity_message(
        kind: str,
        key: str,
        matches: list[Any],
        description: str,
    ) -> str:
        details = ", ".join(f"#{match.number} ({match.url or 'no url'})" for match in matches)
        return (
            f"{kind} {key!r}: multiple remote resources match {description}: {details}. "
            "Remove the duplicate marker/title or fix the state file before retrying."
        )

    def _register(
        self,
        kind: str,
        key: str,
        remote: RemoteIssue | RemoteMilestone,
        matched_by: str,
    ) -> None:
        self._resolved[(kind, key)] = Resolution(remote=remote, matched_by=matched_by)
        if kind == "milestone":
            assert isinstance(remote, RemoteMilestone)
            self._milestone_numbers[key] = remote.number
            if not any(item.id == remote.id for item in self.milestones_index):
                self.milestones_index.append(remote)
        else:
            assert isinstance(remote, RemoteIssue)
            if not any(item.id == remote.id for item in self.issues_index):
                self.issues_index.append(remote)

    def _resolved_milestone(self, spec: MilestoneSpec) -> RemoteMilestone | None:
        remote = self.resolve_milestone(spec).remote
        return remote if isinstance(remote, RemoteMilestone) else None

    def _resolved_issue(self, spec: IssueSpec) -> RemoteIssue | None:
        remote = self.resolve_issue(spec).remote
        return remote if isinstance(remote, RemoteIssue) else None

    def milestone_number_for(self, key: str | None) -> int | None:
        """Return the remote number of a milestone key, when known."""

        if not key:
            return None
        if key in self._milestone_numbers:
            return self._milestone_numbers[key]
        spec = self.manifest.milestone_by_key(key)
        if spec is None:
            return None
        remote = self._resolved_milestone(spec)
        if remote is not None:
            self._milestone_numbers[key] = remote.number
            return remote.number
        return None

    # ------------------------------------------------------------ desired state

    def build_context(
        self,
        *,
        milestone: MilestoneSpec | None,
        issue: IssueSpec | None,
    ) -> dict[str, Any]:
        """Build the restricted Jinja2 context for a document."""

        issues_context = {
            spec.key: issue_ref_dict(spec, self._resolved_issue(spec))
            for spec in self.manifest.issues
        }
        milestone_view: dict[str, Any] | None = None
        if milestone is not None:
            milestone_view = milestone_ref_dict(milestone, self._resolved_milestone(milestone))
        issue_view: dict[str, Any] | None = None
        if issue is not None:
            issue_view = issue_ref_dict(issue, self._resolved_issue(issue))
            if issue.milestone:
                referenced = self.manifest.milestone_by_key(issue.milestone)
                if referenced is not None:
                    milestone_view = milestone_ref_dict(
                        referenced, self._resolved_milestone(referenced)
                    )
        metadata: dict[str, Any] = {}
        if issue is not None:
            metadata = dict(issue.metadata)
        elif milestone is not None:
            metadata = dict(milestone.metadata)
        return {
            "repository": repository_dict(self.config),
            "milestone": milestone_view,
            "issue": issue_view,
            "issues": issues_context,
            "metadata": metadata,
        }

    def desired_milestone(self, spec: MilestoneSpec) -> MilestonePayload:
        """Render the desired remote payload for a milestone."""

        description = ""
        if spec.description_file:
            source = read_document(
                self.root,
                spec.description_file,
                field=f"milestone {spec.key!r} description_file",
            )
            description = self.renderer.render_string(
                source,
                self.build_context(milestone=spec, issue=None),
                origin=spec.description_file,
            )
        return MilestonePayload(
            title=spec.title,
            description=markers.ensure_marker(description, "milestone", spec.key),
            state=spec.state,
            due_on=spec.due_on,
        )

    def desired_issue(self, spec: IssueSpec) -> IssuePayload:
        """Render the desired remote payload for an issue.

        The payload is rebuilt during ``publish`` so that dependency links can
        include remote numbers resolved earlier in the same run.
        """

        source = read_document(
            self.root,
            spec.body_file,
            field=f"issue {spec.key!r} body_file",
        )
        rendered = self.renderer.render_string(
            source,
            self.build_context(milestone=None, issue=spec),
            origin=spec.body_file,
        )
        return IssuePayload(
            title=spec.title,
            body=markers.ensure_marker(rendered, "issue", spec.key),
            state=spec.state,
            labels=list(spec.labels),
            assignees=list(spec.assignees),
            milestone_number=self.milestone_number_for(spec.milestone),
        )

    # ------------------------------------------------------------------- plan

    def plan(
        self,
        *,
        only: set[str] | None = None,
        kinds: set[str] | None = None,
    ) -> Plan:
        """Build a plan describing every create/update/skip action."""

        plan = Plan()
        want_milestones = kinds is None or "milestone" in kinds
        want_issues = kinds is None or "issue" in kinds

        selected_milestones = (
            [spec for spec in self.manifest.milestones if only is None or spec.key in only]
            if want_milestones
            else []
        )
        selected_issues = (
            [spec for spec in self.manifest.issues if only is None or spec.key in only]
            if want_issues
            else []
        )

        self.load_remote()

        if selected_issues:
            known_labels = {name.casefold() for name in self.provider.list_labels()}
            missing_labels: list[str] = []
            for issue_spec in selected_issues:
                for label in issue_spec.labels:
                    if label.casefold() not in known_labels and label not in missing_labels:
                        missing_labels.append(label)
            plan.missing_labels = missing_labels

            assignees: list[str] = []
            for issue_spec in selected_issues:
                for name in issue_spec.assignees:
                    if name not in assignees:
                        assignees.append(name)
            if assignees:
                plan.missing_assignees = self.provider.check_assignees(assignees)

        for milestone_spec in selected_milestones:
            plan.milestones.append(
                self._plan_milestone(milestone_spec, self.resolve_milestone(milestone_spec))
            )

        if selected_issues:
            order = topological_order(
                {spec.key: list(spec.depends_on) for spec in self.manifest.issues}
            )
            selected_keys = {spec.key for spec in selected_issues}
            for key in order:
                if key not in selected_keys:
                    continue
                lookup = self.manifest.issue_by_key(key)
                if lookup is None:  # pragma: no cover - guarded by topological order
                    continue
                plan.issues.append(self._plan_issue(lookup, self.resolve_issue(lookup)))

        return plan

    def _plan_milestone(self, spec: MilestoneSpec, resolution: Resolution) -> ResourcePlan:
        payload = self.desired_milestone(spec)
        resource = ResourcePlan(
            kind="milestone",
            key=spec.key,
            title=spec.title,
            action="create",
            matched_by=resolution.matched_by,
            remote=resolution.remote,
            payload=payload,
        )
        remote = resolution.remote
        if not isinstance(remote, RemoteMilestone):
            return resource
        resource.remote_id = remote.id
        resource.remote_number = remote.number
        resource.changes = diff.diff_milestone(payload, remote)
        if resource.changes:
            resource.action = "update"
            self._check_conflict(resource, "milestone", spec.key, remote.updated_at)
        else:
            resource.action = "skip"
        return resource

    def _plan_issue(self, spec: IssueSpec, resolution: Resolution) -> ResourcePlan:
        payload = self.desired_issue(spec)
        resource = ResourcePlan(
            kind="issue",
            key=spec.key,
            title=spec.title,
            action="create",
            matched_by=resolution.matched_by,
            remote=resolution.remote,
            payload=payload,
        )
        remote = resolution.remote
        if not isinstance(remote, RemoteIssue):
            return resource
        resource.remote_id = remote.id
        resource.remote_number = remote.number
        milestone_spec = self.manifest.milestone_by_key(spec.milestone) if spec.milestone else None
        resource.changes = diff.diff_issue(
            payload,
            remote,
            desired_milestone_title=milestone_spec.title if milestone_spec else None,
        )
        if resource.changes:
            resource.action = "update"
            self._check_conflict(resource, "issue", spec.key, remote.updated_at)
        else:
            resource.action = "skip"
        return resource

    def _check_conflict(
        self,
        resource: ResourcePlan,
        kind: str,
        key: str,
        remote_updated_at: Any,
    ) -> None:
        entry = self.state.bucket(kind).get(key)
        if entry is None or entry.updated_at is None or remote_updated_at is None:
            return
        recorded = diff.normalize_timestamp(entry.updated_at)
        current = diff.normalize_timestamp(remote_updated_at)
        if recorded != current:
            resource.conflict = True
            resource.conflict_detail = (
                f"remote changed at {current.isoformat() if current else '?'} but the last "
                f"recorded sync was {recorded.isoformat() if recorded else '?'}"
            )

    # -------------------------------------------------------------- preflight

    def preflight_problems(
        self,
        plan: Plan,
        *,
        create_missing_labels: bool = False,
        force_update: bool = False,
    ) -> list[PreflightProblem]:
        """Return every problem that prevents the plan from being applied."""

        problems: list[PreflightProblem] = []
        if plan.missing_labels and not create_missing_labels:
            problems.append(
                PreflightProblem(
                    kind="labels",
                    message=(
                        "missing labels: "
                        + ", ".join(plan.missing_labels)
                        + " (create them in the repository or pass --create-missing-labels)"
                    ),
                )
            )
        if plan.missing_assignees:
            problems.append(
                PreflightProblem(
                    kind="assignees",
                    message="unknown assignees: " + ", ".join(plan.missing_assignees),
                )
            )
        conflicts = plan.conflicts()
        if conflicts and not force_update:
            details = "\n".join(
                f"  - {resource.kind} {resource.key!r} (#{resource.remote_number}): "
                f"{resource.conflict_detail}"
                for resource in conflicts
            )
            problems.append(
                PreflightProblem(
                    kind="conflict",
                    message=(
                        "remote resources changed outside repo-planner:\n"
                        + details
                        + "\nre-run with --force-update to overwrite the remote versions"
                    ),
                )
            )
        return problems

    def ensure_ready(
        self,
        plan: Plan,
        *,
        create_missing_labels: bool = False,
        force_update: bool = False,
    ) -> None:
        """Raise a domain error when the plan cannot be applied."""

        problems = self.preflight_problems(
            plan, create_missing_labels=create_missing_labels, force_update=force_update
        )
        if not problems:
            return
        message = "\n\n".join(problem.message for problem in problems)
        if any(problem.kind == "conflict" for problem in problems):
            raise RemoteConflictError(message)
        raise ManifestValidationError(message)

    # ---------------------------------------------------------------- execute

    def execute(
        self,
        plan: Plan,
        *,
        create_missing_labels: bool = False,
        force_update: bool = False,
    ) -> ExecutionSummary:
        """Apply a plan. Mutations happen resource by resource, milestones first."""

        self.ensure_ready(
            plan, create_missing_labels=create_missing_labels, force_update=force_update
        )
        summary = ExecutionSummary()

        if create_missing_labels and plan.missing_labels:
            for label in plan.missing_labels:
                self.provider.create_label(label)
                summary.created_labels.append(label)
                summary.messages.append(f"created label {label!r}")

        for resource in plan.milestones:
            self._execute_milestone(resource, summary)
        for resource in plan.issues:
            self._execute_issue(resource, summary)

        self.save_state()
        return summary

    def _execute_milestone(self, resource: ResourcePlan, summary: ExecutionSummary) -> None:
        assert isinstance(resource.payload, MilestonePayload)
        if resource.action == "create":
            remote = self.provider.create_milestone(resource.payload)
            summary.created_milestones += 1
            summary.messages.append(f"created milestone {resource.key} (#{remote.number})")
        elif resource.action == "update":
            assert resource.remote_number is not None
            remote = self.provider.update_milestone(resource.remote_number, resource.payload)
            summary.updated_milestones += 1
            note = " (forced over remote conflict)" if resource.conflict else ""
            summary.messages.append(f"updated milestone {resource.key} (#{remote.number}){note}")
        else:
            assert isinstance(resource.remote, RemoteMilestone)
            remote = resource.remote
            summary.skipped += 1
        self._register("milestone", resource.key, remote, resource.matched_by or "state")
        self._record("milestone", resource.key, remote)
        if resource.action != "skip":
            self.save_state()

    def _execute_issue(self, resource: ResourcePlan, summary: ExecutionSummary) -> None:
        spec = self.manifest.issue_by_key(resource.key)
        if spec is None:  # pragma: no cover - plan only contains manifest keys
            return
        if resource.action == "create":
            payload = self.desired_issue(spec)
            remote = self.provider.create_issue(payload)
            summary.created_issues += 1
            summary.messages.append(f"created issue {resource.key} (#{remote.number})")
        elif resource.action == "update":
            assert resource.remote_number is not None
            payload = self.desired_issue(spec)
            remote = self.provider.update_issue(resource.remote_number, payload)
            summary.updated_issues += 1
            note = " (forced over remote conflict)" if resource.conflict else ""
            summary.messages.append(f"updated issue {resource.key} (#{remote.number}){note}")
        else:
            assert isinstance(resource.remote, RemoteIssue)
            remote = resource.remote
            summary.skipped += 1
        self._register("issue", resource.key, remote, resource.matched_by or "state")
        self._record("issue", resource.key, remote)
        if resource.action != "skip":
            self.save_state()
