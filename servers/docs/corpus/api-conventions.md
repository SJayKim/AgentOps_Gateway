# API Conventions

Internal services expose JSON over HTTP. Use snake_case for field names,
ISO8601 for timestamps, and integer identifiers. Errors return a structured
body with a human readable message. Breaking changes require a version bump
and a migration note in the changelog.
