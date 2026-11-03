# Repository workflow

- After completing user-requested code or documentation changes, run the relevant checks, commit the intended source files, and push to `origin/main` without asking again.
- Use the Git proxy configured for the Codex environment. Check the current Git proxy setting instead of hardcoding an address.
- Keep `input_file/`, `data/`, `output_file/`, generated test files, and local runtime dependencies out of Git. Review staged files before every public push.
- If authentication, network access, or branch rules prevent a push, report the exact blocker and leave the local commit intact.

## Department portal access

- Portal users are department members with equal business capabilities. Do not add reader/editor roles or write-group checks.
- Keep trusted identity for audit, same-origin/proxy validation, and the department access boundary. AllowWrites/allow_writes is a service-wide maintenance switch, not a user role.
- Do not apply IIS/security configuration or deploy automatically. Portal preview writes only to isolated snapshots and never to original business data.
