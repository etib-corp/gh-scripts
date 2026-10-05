# repo-planner

Declarative CLI for managing GitHub and GitLab milestones and issues from JSON
manifests. Local manifests are the desired state; `repo-planner` validates them,
computes diffs against the remote repository, and creates or updates resources
without ever creating duplicates.

- Provider-agnostic domain model with GitHub and GitLab adapters.
- Stable identity markers prevent duplicate resources.
- Dry-run by default; every remote mutation requires `--apply`.
- Conflict detection: remote changes made outside the tool are never
  overwritten unless `--force-update` is used.
- Long-form content lives in Markdown templates rendered with a restricted
  Jinja2 environment.
- Issue dependencies are resolved with a topological sort and cycle detection.
- Explicit, interactive import for resources created before this tool existed.

## Requirements

- Python 3.12 or newer.
- An API token with read/write access to the target repository.

## Installation

```console
$ python3.12 -m venv .venv
$ .venv/bin/python -m pip install -e ".[dev]"   # editable, for development
```

This installs the `repo-planner` console script. For a non-editable install
(used in scripts or CI), run `.venv/bin/python -m pip install .` instead.

> **Note (macOS):** if the project lives in an iCloud-synced folder (e.g.
> `~/Documents`), the sync service may mark the editable-install `.pth` file as
> hidden. Python ≥ 3.13.6 deliberately ignores hidden `.pth` files, so the
> console script may start failing with `ModuleNotFoundError: No module named
> 'repo_planner'`. Fix it with
> `chflags nohidden .venv/lib/python*/site-packages/_editable_impl_repo_planner.pth`,
> reinstall non-editable (`pip install .`), or run the current sources with
> `env PYTHONPATH=src .venv/bin/python -m repo_planner ...`.

## Quick start

```console
$ repo-planner init --provider github --repository owner/repo
$ $EDITOR configs/manifest.json          # declare milestones and issues
$ repo-planner validate                  # offline checks (paths, templates, keys)
$ repo-planner plan                      # structured diff against the remote repo
$ repo-planner publish all --apply --yes # create/update remote resources
```

`init` can also detect the repository from the `origin` git remote when
`--repository` is omitted.

## Authentication

Tokens are read from environment variables and are never written to disk:

| Provider | Default variable | Typical scopes |
| -------- | ---------------- | -------------- |
| GitHub   | `GITHUB_TOKEN`   | `repo` (classic) or Issues/Milestones read-write (fine-grained) |
| GitLab   | `GITLAB_TOKEN`   | `api` (or `read_api` + project access for read-only plans) |

```console
$ export GITHUB_TOKEN=ghp_...          # fish: set -x GITHUB_TOKEN ghp_...
$ export GITLAB_TOKEN=glpat-...
```

Override the variable name with `token_env` and point at a self-hosted instance
with `api_url` (for GitHub Enterprise use e.g.
`https://ghe.example.com/api/v3`).

Secrets never appear in logs, plans, state files or error messages. Debug
output only ever contains method names and paths, and header redaction helpers
are used if headers are ever inspected.

## Minimal configuration

`configs/repo-planner.json`:

```json
{
  "provider": "github",
  "repository": "owner/repo",
  "manifest": "configs/manifest.json",
  "state": "configs/state.json",
  "milestone_cache": "configs/milestones.cache.json",
  "api_url": null,
  "token_env": null
}
```

| Key | Default | Description |
| --- | --- | --- |
| `provider` | `"github"` | `"github"` or `"gitlab"` |
| `repository` | required | `owner/repo`; GitLab supports nested groups (`group/sub/repo`) |
| `manifest` | `configs/manifest.json` | manifest path, relative to the project root |
| `state` | `configs/state.json` | state file path (managed by the tool) |
| `milestone_cache` | `configs/milestones.cache.json` | cache written by `sync milestones` |
| `api_url` | provider default | API base URL for self-hosted instances |
| `token_env` | provider default | environment variable holding the token |

Paths must stay inside the project root; `..` segments and symlinks that escape
it are rejected.

## Manifests

`configs/manifest.json` declares the desired state. All keys must be unique and
match `[A-Za-z0-9][A-Za-z0-9._-]*`.

```json
{
  "version": 1,
  "milestones": [
    {
      "key": "m-2026-q1",
      "title": "2026 Q1",
      "description_file": "templates/milestone.md",
      "due_on": "2026-03-31",
      "state": "open",
      "metadata": { "owner": "platform" }
    }
  ],
  "issues": [
    {
      "key": "auth-oidc",
      "title": "Implement OIDC login",
      "body_file": "templates/issue-oidc.md",
      "labels": ["feature", "auth"],
      "milestone": "m-2026-q1",
      "assignees": ["octocat"],
      "state": "open",
      "depends_on": [],
      "metadata": {}
    }
  ]
}
```

Milestone fields: `key`, `title`, `description_file`, `due_on`, `state`,
`metadata`.

Issue fields: `key`, `title`, `body_file`, `labels`, `milestone`, `assignees`,
`state`, `depends_on`, `metadata`.

Markdown paths are relative to the project root; the files are rendered with
Jinja2 and the identity marker is appended automatically if missing.

## Templates

Documents are rendered with a sandboxed Jinja2 environment
(`StrictUndefined`, no file loader, no Python internals). The context is
explicit:

```markdown
# {{ milestone.title }}

Due {{ milestone.due_on }}.

{% if issue %}
This issue closes {{ issue.key }} ({{ issue.labels | join(", ") }}).
{% endif %}

{% for dep in issue.depends_on %}
- depends on {{ issues[dep].title }}{% if issues[dep].url %} ({{ issues[dep].url }}){% endif %}
{% endfor %}
```

| Variable | Description |
| --- | --- |
| `repository` | `provider`, `full_name`, `namespace`, `name`, `url` |
| `milestone` | the milestone being rendered, or the issue's milestone |
| `issue` | the issue being rendered (`null` for milestone templates) |
| `issues` | mapping of every manifest issue key to its reference |
| `metadata` | `metadata` of the resource being rendered |

Issue references expose `key`, `title`, `state`, `labels`, `assignees`,
`depends_on`, `milestone`, `metadata` and — once resolved — `id`, `number`,
`url`. Dependency links are included whenever a dependency has been resolved
(resources created earlier in the same run are available to later templates).

## Identity markers and state

Every managed remote description ends with a stable marker:

```html
<!-- repo-planner:issue:<key> -->
<!-- repo-planner:milestone:<key> -->
```

Remote resources are resolved in this order:

1. the remote ID recorded in `configs/state.json`;
2. a matching identity marker in the description/body;
3. exact title matching (last resort);
4. multiple matches cause an actionable ambiguity error;
5. creation only happens when no reliable match exists.

`configs/state.json` maps manifest keys to remote IDs and the remote
`updated_at` timestamps. It is written atomically (temporary file + rename) and
can be committed to share mappings with your team.

### Conflict handling

Before updating a resource, the tool compares the remote `updated_at` timestamp
with the one recorded during the previous successful run. If the remote resource
changed outside `repo-planner`:

- `plan` reports the conflict;
- `publish` refuses to apply the plan and exits non-zero;
- `--force-update` explicitly allows overwriting the remote version.

## CLI reference

```text
repo-planner [--root DIR] [--config PATH] [--verbose] COMMAND ...
```

| Command | Purpose |
| --- | --- |
| `init` | scaffold `configs/` (configuration, manifest, state) |
| `validate` | offline validation: schema, paths, templates, dependencies |
| `plan` | structured diff against the remote repository; never mutates |
| `sync milestones` | write a local cache of remote milestones; never mutates remotes |
| `import-existing` | attach existing remote resources to manifest keys |
| `publish milestones\|issues\|all` | create/update remote resources (dry-run unless `--apply`) |

Common options (available on every command):

| Option | Description |
| --- | --- |
| `--root DIR` | project root (default: current directory) |
| `--config PATH` | configuration file (default: `<root>/configs/repo-planner.json`) |
| `--verbose` | show detailed output, including unchanged resources |

Mutation options (available on `init`, `sync`, `import-existing`, `publish`;
`plan` accepts `--only`):

| Option | Description |
| --- | --- |
| `--dry-run` | preview without writing anything (publish default) |
| `--apply` | required for `publish` to mutate remotes; explicit no-op elsewhere |
| `--yes` | assume yes for confirmation and selection prompts (required in CI) |
| `--only KEY` | limit the operation to KEY (repeatable, commas accepted) |
| `--force-update` | overwrite remote resources that changed since the last run |
| `--create-missing-labels` | (`publish` only) create missing labels first |

Command-specific options:

| Command | Option | Description |
| --- | --- | --- |
| `init` | `--provider github\|gitlab` | repository host (default: detected, else github) |
| `init` | `--repository OWNER/NAME` | repository to manage (default: detected git remote) |
| `init` | `--force` | overwrite existing configuration files |
| `import-existing` | `--kind all\|milestones\|issues` | restrict what to import |

Exit codes: `0` success, `1` any failure (validation error, conflict, provider
error), `2` usage error, `130` interrupted.

## Dry-run workflow

`publish` never mutates anything unless `--apply` is passed:

```console
$ repo-planner plan
plan: 2 to create, 1 to update, 3 unchanged
create milestone "m-2026-q1" "2026 Q1"
  description: 14 lines
  due_on: 2026-03-31
update issue "auth-oidc" (#12)
  labels: +docs -wontfix
  body: 3 changed, 2 added lines
```

Dry runs exit non-zero when publishing would fail (missing labels, unknown
assignees, unresolved conflicts), which makes them useful as CI gates.

## Applying a plan

```console
$ repo-planner publish all --apply
plan: 2 to create, 1 to update, 3 unchanged
apply 3 change(s)? [y/N] y
created milestone m-2026-q1 (#4)
created issue auth-oidc (#12)
updated issue auth-oidc (#12)
done: 2 created, 1 updated, 3 unchanged
```

In non-interactive sessions pass `--yes`. Milestones are published before
issues, and issues follow their dependency order, so templates referencing
newly created dependencies receive real links.

## Syncing milestones

```console
$ repo-planner sync milestones
cached 12 milestone(s) to /path/to/project/configs/milestones.cache.json
```

The cache contains the remote milestones, the fetch timestamp and a
`managed` mapping of identity-marker keys to milestone numbers. Only the local
cache is written; remote resources are never touched.

## Importing existing resources

For repositories that predate `repo-planner`, `import-existing` attaches
existing resources to manifest keys. A resource is attached only after a
reliable identity-marker match or an explicit interactive selection — title
similarity alone is never used automatically:

```console
$ repo-planner import-existing --kind issues
issue 'auth-oidc' "Implement OIDC login" has no identity marker in the remote repository.
  1) #12 'Implement OIDC login' (open)
  or type the number of any remote resource, 's' to skip
attach issue 'auth-oidc' to which resource? 1
attached issue 'auth-oidc' -> #12 (explicit selection)
updated /path/to/project/configs/state.json
```

Non-interactively (`--yes` or no TTY) only marker matches are attached; the
rest are reported as skipped. Use `--dry-run` to preview.

## Dependency ordering

`depends_on` is validated for unknown keys and cycles. Cyclic manifests fail
with the list of involved issue keys. When publishing, dependencies are created
first and their links can be referenced from dependent issue templates.

## Labels and assignees

Referenced labels and assignees are validated against the remote repository
before anything is mutated:

- missing labels fail the run; create them with `--create-missing-labels`;
- unknown assignees always fail; they are never silently dropped.

## GitHub vs GitLab differences

| Aspect | GitHub | GitLab |
| --- | --- | --- |
| Repository | `owner/repo` | `namespace/project`, nested groups supported |
| State values | `open` / `closed` | `opened` / `closed` (milestones: `active` / `closed`) |
| Issue body field | `body` | `description` |
| Milestone due date | `due_on` (ISO timestamp) | `due_date` (`YYYY-MM-DD`) |
| Assignees | logins | usernames resolved to user IDs |
| Labels | array | comma-separated string |
| Clearing a milestone | `milestone: null` | `milestone_id: 0` |
| Rate limits | 403 with `X-RateLimit-Remaining: 0` | 429 / `Retry-After` |

The CLI behaves identically on both; the adapters translate payloads and state
names. Shared HTTP handling retries transient failures with exponential
backoff, always respects rate-limit responses, and never retries
non-idempotent requests after they may have been executed.

## CI usage example

```yaml
name: repo-planner
on:
  push:
    paths: [configs/**, templates/**]

jobs:
  plan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install .
      - run: repo-planner plan
        env:
          GITHUB_TOKEN: ${{ secrets.REPO_PLANNER_TOKEN }}

  publish:
    if: github.ref == 'refs/heads/main'
    needs: plan
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install .
      - run: repo-planner publish all --apply --yes
        env:
          GITHUB_TOKEN: ${{ secrets.REPO_PLANNER_TOKEN }}
```

## Development

```console
$ .venv/bin/python -m pytest      # 139 tests, HTTP fully mocked
$ .venv/bin/python -m ruff check .
$ .venv/bin/python -m ruff format --check .
$ .venv/bin/python -m mypy src
```

Tests run directly against `src/` (`pythonpath` is configured in
`pyproject.toml`), so they always exercise the working tree. The installed
console script reflects the last `pip install`; reinstall it
(`.venv/bin/python -m pip install . --no-deps`) after editing, or run the live
sources with `env PYTHONPATH=src .venv/bin/python -m repo_planner`.

Integration tests that talk to real providers are intentionally not part of the
default suite; normal test runs never touch GitHub or GitLab.

## Project layout

```text
src/repo_planner/
├── cli.py           # argument parsing and command dispatch
├── config.py        # project configuration, token handling, git detection
├── errors.py        # domain-specific exceptions
├── models.py        # Pydantic models (manifests, payloads, state)
├── manifest.py      # manifest loading and offline document validation
├── reconciler.py    # resolution, planning and execution engine
├── providers/       # base.py, github.py, gitlab.py
├── http.py          # retrying HTTP client with redaction helpers
├── templates.py     # sandboxed Jinja2 renderer
├── markers.py       # identity markers
├── paths.py         # path traversal protection
├── dependencies.py  # topological ordering and cycle detection
├── diff.py          # normalization and field-level diffs
├── state.py         # atomic state persistence
├── sync.py          # remote milestone cache
├── importer.py      # import-existing
└── report.py        # human-readable plans
```

A ready-to-run example project lives in [`examples/demo`](examples/demo).
