"""Stable, user-actionable error hierarchy.

Every error LSW can surface derives from :class:`LswError` so the presentation
layer can map it to a stable exit code (see :mod:`lsw.exit_codes`) and a short,
actionable message. The hierarchy follows the task specification.
"""

from __future__ import annotations

from .exit_codes import (
    EXIT_BACKEND_UNAVAILABLE,
    EXIT_CONFIGURATION,
    EXIT_INSTANCE_BUSY,
    EXIT_INSTANCE_CONFLICT,
    EXIT_INSTANCE_NOT_FOUND,
    EXIT_OPERATION_FAILED,
    EXIT_SECURITY,
    EXIT_TIMEOUT,
    EXIT_USAGE,
)


class LswError(Exception):
    """Base class for expected, user-actionable errors."""

    exit_code: int = EXIT_OPERATION_FAILED

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UsageError(LswError):
    exit_code = EXIT_USAGE


class ConfigurationError(LswError):
    exit_code = EXIT_CONFIGURATION


class DependencyMissingError(LswError):
    exit_code = EXIT_BACKEND_UNAVAILABLE


class InvalidInstanceNameError(LswError):
    exit_code = EXIT_USAGE


class InstanceNotFoundError(LswError):
    exit_code = EXIT_INSTANCE_NOT_FOUND


class InstanceAlreadyExistsError(LswError):
    exit_code = EXIT_INSTANCE_CONFLICT


class InstanceBusyError(LswError):
    exit_code = EXIT_INSTANCE_BUSY


class InstanceCorruptError(LswError):
    exit_code = EXIT_OPERATION_FAILED


class BackendError(LswError):
    exit_code = EXIT_BACKEND_UNAVAILABLE


class OperationError(LswError):
    exit_code = EXIT_OPERATION_FAILED


class OperationTimeoutError(LswError):
    exit_code = EXIT_TIMEOUT


class UnsafePathError(LswError):
    exit_code = EXIT_SECURITY


class ConfirmationRequiredError(LswError):
    exit_code = EXIT_SECURITY


def exit_code_for(error: LswError) -> int:
    """Map a domain error to its stable process exit code."""
    return error.exit_code
