from api.scan import _authorized


def test_scan_endpoint_requires_configured_bearer_secret():
    assert not _authorized(None, "secret")
    assert not _authorized("Bearer wrong", "secret")
    assert not _authorized("Bearer secret", None)
    assert _authorized("Bearer secret", "secret")
