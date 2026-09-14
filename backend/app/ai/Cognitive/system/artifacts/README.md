# Model artifacts

Offline training writes versioned JSON artifacts here (or to another configured
path). Generated artifacts should be reviewed before deployment. Runtime loading is
lazy; a missing or invalid artifact safely falls back to deterministic rules.
