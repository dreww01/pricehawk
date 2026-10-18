"""Application errors independent of the HTTP presentation layer."""


class AlertOperationError(Exception):
    """An expected alert operation failure with a stable public message."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code
