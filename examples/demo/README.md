# Demo project

A complete, minimal project for `repo-planner`. Run everything from this
directory:

```console
$ cd examples/demo
$ export GITHUB_TOKEN=...        # never commit tokens
$ repo-planner validate          # offline: schema, paths, templates
$ repo-planner plan              # read-only diff against the remote repository
$ repo-planner publish all --apply --yes
```

Try the workflow without touching a real repository by pointing the mock-free
commands at an empty one, or start with:

```console
$ repo-planner validate
$ repo-planner plan
```

Files:

- `configs/repo-planner.json` – provider and repository configuration.
- `configs/manifest.json` – desired milestones and issues.
- `templates/` – Jinja2 Markdown bodies; the identity marker is appended
  automatically on publish.
