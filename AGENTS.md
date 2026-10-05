# AGENTS.md

## Project purpose

This repository provides a declarative Python CLI for managing GitHub and GitLab milestones and issues.

Users define milestones and issues in JSON manifests. Long-form content is stored in Markdown files referenced by those manifests. The CLI validates, plans, creates, updates, and synchronizes remote resources while preventing accidental duplicates.

The project must support both GitHub and GitLab through provider adapters.

## Core principles

- Keep the domain model provider-agnostic.
- Keep GitHub and GitLab API details inside `src/repo_planner/providers/`.
- Treat local manifests as the desired state.
- Never create duplicates when a managed remote resource already exists.
- Prefer stable machine identifiers over title-based matching.
- Preserve human changes when a conflict is detected unless an explicit force flag is used.
- Do not expose tokens or secrets in logs, manifests, cache files, test fixtures, or documentation.
- Make every mutating command safe by default.

## Resource identity

Every managed issue and milestone has a stable `key` in its JSON manifest.

Managed remote descriptions must include an HTML marker:

```html
<!-- repo-planner:issue:<key> -->
<!-- repo-planner:milestone:<key> -->
```

Resolution order for a remote resource:

1. Use the resource ID stored in `configs/state.json`.
2. Search for the marker in the remote description or body.
3. Use exact title matching only as a fallback.
4. If multiple matches are found, fail with an actionable ambiguity error.
5. Create the remote resource only when no reliable match exists.

Never use fuzzy title matching to update a remote resource.

## CLI requirements

The CLI must expose these commands:

```text
repo-planner init
repo-planner validate
repo-planner plan
repo-planner sync milestones
repo-planner import-existing
repo-planner publish milestones
repo-planner publish issues
repo-planner publish all
```

Mutating commands must support:

- `--dry-run`
- `--apply`
- `--yes`
- `--only`
- `--force-update`
- `--verbose`

`plan` must never make remote changes.

`publish` must default to dry-run behavior unless `--apply` is explicitly supplied.

## Manifest requirements

JSON manifests must be validated with Pydantic models before templates are rendered or API calls are made.

Issue manifests support:

- `key`
- `title`
- `body_file`
- `labels`
- `milestone`
- `assignees`
- `state`
- `depends_on`
- `metadata`

Milestone manifests support:

- `key`
- `title`
- `description_file`
- `due_on`
- `state`
- `metadata`

Markdown file paths must be relative to the repository root and must not escape it.

## Template rendering

Use Jinja2 with a restricted, explicit context.

Supported template context should include:

- `repository`
- `milestone`
- `issue`
- `issues`
- `metadata`

Do not allow arbitrary Python execution or unsafe Jinja environment features.

Render templates before calculating diffs. Always append the resource identity marker if it is absent.

## State and synchronization

`configs/state.json` stores mappings between manifest keys and remote IDs.

Do not manually edit remote IDs into manifests.

When a remote resource is created or successfully updated, atomically update the state file. Use a temporary file followed by an atomic rename to avoid corrupting local state.

`sync milestones` must write a cache of remote milestones without changing remote resources.

`import-existing` must only attach existing remote resources to manifest keys after explicit user selection or a reliable marker match.

## Reconciliation rules

For each desired resource:

1. Resolve the corresponding remote resource.
2. Render the desired body or description.
3. Compare normalized desired and remote fields.
4. Print a structured diff in plan mode.
5. Create, update, or skip the resource.
6. Persist the resolved remote ID and timestamp.

Normalize comparison values where appropriate:

- Sort labels before comparison.
- Normalize line endings.
- Preserve meaningful Markdown whitespace.
- Normalize dates to ISO-8601.
- Treat missing optional values consistently.

Do not update a remote resource when no meaningful change exists.

## Conflict handling

Store the remote `updated_at` timestamp in local state.

Before modifying a managed resource, compare its current remote timestamp with the timestamp recorded during the previous successful run.

If the remote resource changed outside the tool:

- Report a conflict.
- Do not overwrite the remote resource by default.
- Require `--force-update` to proceed.
- Include enough information for the user to decide safely.

## Labels and assignees

Validate referenced labels and assignees before publishing.

Missing labels must cause validation to fail by default.

Labels may only be created when `--create-missing-labels` is explicitly specified.

Never silently drop unavailable labels or assignees.

## Dependencies

Issue dependencies must be resolved before rendering issue templates that refer to other issues.

Use a topological sort for `depends_on`.

If a dependency cycle exists, fail with a clear list of the involved issue keys.

When supported by the provider, include links to resolved dependency issues in the rendered body. Do not assume that GitHub and GitLab support identical issue relation APIs.

## Provider adapters

Provider code belongs under:

```text
src/repo_planner/providers/
```

Required modules:

```text
base.py
github.py
gitlab.py
```

The shared `Provider` interface must own operations such as:

- list labels
- list issues
- list milestones
- create issue
- update issue
- create milestone
- update milestone
- get remote resource by ID
- search managed resources by marker

Do not place provider-specific conditionals in command modules when an adapter method can express the behavior.

## Error handling

Raise domain-specific exceptions such as:

- `ConfigurationError`
- `ManifestValidationError`
- `TemplateRenderError`
- `ResourceNotFoundError`
- `AmbiguousResourceError`
- `RemoteConflictError`
- `ProviderAuthenticationError`
- `ProviderRateLimitError`

CLI output must be concise and actionable.

Use non-zero exit codes on failures.

Do not print raw access tokens, authorization headers, or full HTTP request headers.

## HTTP behavior

Use `httpx` for HTTP communication.

Implement:

- Timeouts.
- Retries with exponential backoff for transient failures.
- Respect for rate-limit responses.
- Clear handling for authentication and authorization failures.
- Structured request context in debug logs, with secrets redacted.

Do not retry non-idempotent requests unless resource identity handling makes the operation safe.

## Testing

Use pytest.

Include unit tests for:

- Pydantic manifest validation.
- Path traversal protection for Markdown files.
- Marker insertion and discovery.
- Resource resolution order.
- Diff normalization.
- Dependency ordering and cycle detection.
- Conflict detection.
- State file atomic writes.
- GitHub and GitLab payload conversion.

Mock HTTP requests. Do not call real GitHub or GitLab APIs from normal test runs.

Add integration tests only when credentials are supplied explicitly through environment variables.

## Code quality

Target Python 3.12 or newer.

Use:

- Type hints for all public functions and methods.
- Dataclasses or Pydantic models for structured data.
- Ruff for linting and formatting.
- Mypy for type checking where practical.
- Small functions with explicit inputs and outputs.

Avoid global mutable state.

Avoid `Any` unless it is justified at an API boundary.

Prefer composition over inheritance, except for the provider interface.

## Documentation

Keep the README updated with:

- Installation.
- Authentication setup.
- Minimal configuration.
- Example manifests.
- Dry-run workflow.
- Applying a plan.
- Importing existing issues.
- GitHub and GitLab setup differences.
- CI usage example.

Every new CLI option must be documented.

## Definition of done

A change is complete only if:

1. Relevant tests pass.
2. Manifest validation is updated when the schema changes.
3. `plan` accurately describes mutations.
4. `--dry-run` does not mutate remote or local state.
5. Remote resource identity is stable and duplicate-safe.
6. Secrets cannot appear in logs or generated files.
7. Documentation covers user-facing behavior.