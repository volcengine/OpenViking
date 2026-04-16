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


class AGFSNotFoundError(AGFSClientError):
    """File or directory not found"""

    pass


class AGFSAlreadyExistsError(AGFSClientError):
    """File or directory already exists"""

    pass


class AGFSFileExistsError(AGFSAlreadyExistsError):
    """File already exists (alias for AGFSAlreadyExistsError)"""

    pass


class AGFSPermissionDeniedError(AGFSClientError):
    """Permission denied"""

    pass


class AGFSInvalidPathError(AGFSClientError):
    """Invalid path"""

    pass


class AGFSNotADirectoryError(AGFSClientError):
    """Not a directory"""

    pass


class AGFSIsADirectoryError(AGFSClientError):
    """Is a directory (when file operation expected)"""

    pass


class AGFSDirectoryNotEmptyError(AGFSClientError):
    """Directory not empty"""

    pass


class AGFSInvalidOperationError(AGFSClientError):
    """Invalid operation"""

    pass


class AGFSIoError(AGFSClientError):
    """I/O error"""

    pass


class AGFSConfigError(AGFSClientError):
    """Configuration error"""

    pass


class AGFSMountPointNotFoundError(AGFSClientError):
    """Mount point not found"""

    pass


class AGFSMountPointExistsError(AGFSClientError):
    """Mount point already exists"""

    pass


class AGFSSerializationError(AGFSClientError):
    """Serialization error"""

    pass


class AGFSNetworkError(AGFSClientError):
    """Network error"""

    pass


class AGFSInternalError(AGFSClientError):
    """Internal error"""

    pass


class AGFSPluginError(AGFSClientError):
    """Plugin error"""

    pass
