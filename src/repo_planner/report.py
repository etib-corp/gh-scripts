"""Human-readable rendering of plans and diffs."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from .diff import summarize_text_change
from .models import IssuePayload, MilestonePayload
from .reconciler import Plan, ResourcePlan

_VALUE_LIMIT = 60


def render_plan(plan: Plan, *, verbose: bool = False) -> list[str]:
    """Render a plan as a list of printable lines."""

    lines: list[str] = []
    creates = plan.with_action("create")
    updates = plan.with_action("update")
    skips = plan.with_action("skip")
    lines.append(
        f"plan: {len(creates)} to create, {len(updates)} to update, {len(skips)} unchanged"
    )

    if plan.missing_labels:
        lines.append(f"missing labels: {', '.join(plan.missing_labels)}")
    if plan.missing_assignees:
        lines.append(f"unknown assignees: {', '.join(plan.missing_assignees)}")

    for resource in plan.resources:
        if resource.action == "skip" and not verbose:
            continue
        lines.append(_action_line(resource))
        if resource.action == "create":
            lines.extend(_create_details(resource))
        else:
            if resource.conflict and resource.conflict_detail:
                lines.append(f"  conflict: {resource.conflict_detail}")
            lines.extend(_change_lines(resource))
    return lines


def _action_line(resource: ResourcePlan) -> str:
    label = f"{resource.kind} {resource.key} {json.dumps(resource.title)}"
    if resource.action == "create":
        return f"create {label}"
    location = f" (#{resource.remote_number})" if resource.remote_number is not None else ""
    if resource.conflict:
        return f"conflict {label}{location}"
    return f"{resource.action} {label}{location}"


def _create_details(resource: ResourcePlan) -> list[str]:
    payload = resource.payload
    lines: list[str] = []
    if isinstance(payload, MilestonePayload):
        lines.append(f"  description: {_line_count(payload.description)} lines")
        if payload.due_on is not None:
            lines.append(f"  due_on: {payload.due_on.isoformat()}")
        lines.append(f"  state: {payload.state}")
    elif isinstance(payload, IssuePayload):
        lines.append(f"  body: {_line_count(payload.body)} lines")
        if payload.labels:
            lines.append(f"  labels: {', '.join(payload.labels)}")
        if payload.assignees:
            lines.append(f"  assignees: {', '.join(payload.assignees)}")
        if payload.milestone_number is not None:
            lines.append(f"  milestone: #{payload.milestone_number}")
        lines.append(f"  state: {payload.state}")
    return lines


def _change_lines(resource: ResourcePlan) -> list[str]:
    lines: list[str] = []
    for change in resource.changes:
        if change.field in {"body", "description"}:
            summary = summarize_text_change(str(change.desired or ""), str(change.remote or ""))
            lines.append(f"  {change.field}: {summary}")
        elif change.field in {"labels", "assignees"}:
            lines.append(f"  {change.field}: {_list_change(change.desired, change.remote)}")
        else:
            lines.append(
                f"  {change.field}: {_format_value(change.remote)} -> "
                f"{_format_value(change.desired)}"
            )
    return lines


def _list_change(desired: Any, remote: Any) -> str:
    desired_map = {str(value).casefold(): value for value in desired or []}
    remote_map = {str(value).casefold(): value for value in remote or []}
    parts = [f"+{desired_map[key]}" for key in desired_map if key not in remote_map]
    parts += [f"-{remote_map[key]}" for key in remote_map if key not in desired_map]
    if parts:
        return " ".join(sorted(parts))
    return "reordered or re-cased"


def _format_value(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, str):
        return json.dumps(_truncate(value))
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) if value else "none"
    return json.dumps(value, default=str)


def _truncate(text: str) -> str:
    if len(text) <= _VALUE_LIMIT:
        return text
    return text[: _VALUE_LIMIT - 3] + "..."


def _line_count(text: str) -> int:
    return len(text.splitlines())
