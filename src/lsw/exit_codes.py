"""Stable LSW process exit codes.

Exit codes ``0`` and ``2..10`` are reserved for LSW itself. Once a Windows
program has successfully started, LSW passes through its exit code instead
(see ``docs/cli.md`` in later milestones).
"""

EXIT_SUCCESS = 0
EXIT_USAGE = 2
EXIT_CONFIGURATION = 3
EXIT_INSTANCE_NOT_FOUND = 4
EXIT_INSTANCE_CONFLICT = 5
EXIT_BACKEND_UNAVAILABLE = 6
EXIT_INSTANCE_BUSY = 7
EXIT_OPERATION_FAILED = 8
EXIT_TIMEOUT = 9
EXIT_SECURITY = 10
