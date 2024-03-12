from functools import wraps
from unittest.mock import MagicMock


def stringify(func):
    @wraps(func)
    def wrapper(*args):
        return str(func(*args))

    return wrapper


class A(object):
    @stringify
    def add_numbers(self, a, b):
        """
        Returns the sum of `a` and `b` as a string.
        """
        return a + b


def test_stringify():
    @stringify
    def func(x):
        return x

    assert func(42) == "42"


def test_A_add_numbers():
    instance = MagicMock(spec=A)
    result = A.add_numbers.__wrapped__(instance, 3, 7)
    assert result == 10
