"""Application version metadata."""

from importlib.metadata import PackageNotFoundError, version

try:
    APPLICATION_VERSION = version("pricehawk")
except PackageNotFoundError:
    # Source checkouts may run before the package is installed.
    APPLICATION_VERSION = "0.1.0"
