"""Exception classes for pyagfs"""


class AGFSClientError(Exception):
    """Base exception for AGFS client errors"""

    pass


class AGFSConnectionError(AGFSClientError):
    """Connection related errors"""

    pass


class AGFSTimeoutError(AGFSClientError):
    """Timeout errors"""

    pass


class AGFSHTTPError(AGFSClientError):
    """HTTP related errors"""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class AGFSNotSupportedError(AGFSClientError):
    """Operation not supported by the server or filesystem (HTTP 501)"""

    pass
