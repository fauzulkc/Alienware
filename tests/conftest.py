import pytest

from x17tune import config


@pytest.fixture(scope="session")
def cfg():
    return config.load()
