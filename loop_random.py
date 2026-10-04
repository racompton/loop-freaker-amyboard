"""Small random API shared by CPython and MicroPython (no random.choices/sample)."""
import random as _random


def random():
    return _random.getrandbits(24) / 16777216.0


def randint(low, high):
    span = high - low + 1
    if span <= 0:
        raise ValueError("empty random range")
    bits = 1
    while (1 << bits) < span:
        bits += 1
    value = _random.getrandbits(bits)
    while value >= span:
        value = _random.getrandbits(bits)
    return low + value


def choice(values):
    return values[randint(0, len(values) - 1)]


def choices(values, weights):
    target = random() * sum(weights)
    for value, weight in zip(values, weights):
        target -= weight
        if target < 0:
            return [value]
    return [values[-1]]


def sample(values, k):
    pool = list(values)
    if k < 0 or k > len(pool):
        raise ValueError("invalid sample size")
    result = []
    for _ in range(k):
        result.append(pool.pop(randint(0, len(pool) - 1)))
    return result
