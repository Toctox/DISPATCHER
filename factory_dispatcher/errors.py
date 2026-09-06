class DispatcherError(RuntimeError):
    """Expected operational failure."""


class ConfigurationError(DispatcherError):
    """Invalid or incomplete configuration."""


class ContractError(DispatcherError):
    """Remote data does not satisfy the mechanical contract."""


class ClaimLostError(DispatcherError):
    """The queue row no longer contains this worker's claim."""


class LaunchGuardError(DispatcherError):
    """A launcher safety guard rejected the request."""
