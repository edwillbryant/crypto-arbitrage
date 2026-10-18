"""Exception hierarchy. Data problems are loud: nothing downstream may run on data that failed a check."""


class AgentError(Exception):
    """Base class for all agent errors."""


class DataIntegrityError(AgentError):
    """Data is malformed, inconsistent or violates point-in-time rules."""


class ChecksumMismatch(DataIntegrityError):
    """A downloaded archive does not match its published checksum."""


class DataConflict(DataIntegrityError):
    """A write would silently change history that is already stored."""


class LookAheadError(AgentError):
    """Something tried to use information from the future."""


class NotFound(AgentError):
    """A remote resource does not exist (e.g. HTTP 404)."""
