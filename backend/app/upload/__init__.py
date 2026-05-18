"""Optional YouTube upload integration.

Imports inside this package are deliberately lazy: `google-api-python-client`
and friends are heavy and may be missing on dev machines that don't need
uploads. The HTTP endpoints surface a "YouTube upload is not configured"
error in that case rather than crashing the import.
"""
