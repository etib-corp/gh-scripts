## Goal

Emit an audit event for every authentication attempt.

Depends on {{ issues['auth-oidc'].title }}{% if issues['auth-oidc'].url %} ({{ issues['auth-oidc'].url }}){% endif %}.

## Acceptance criteria

- [ ] Failed and successful logins are recorded
- [ ] Events are exported to the SIEM
