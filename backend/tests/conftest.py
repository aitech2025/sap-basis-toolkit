import pytest

from app import auth


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    auth._failures.clear()
    yield
    auth._failures.clear()
