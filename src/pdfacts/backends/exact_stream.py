"""Internal bounded-source failure signals, translated at the feature boundary."""


class UnsupportedExactSource(ValueError):
    pass


class ExactSourceLimit(RuntimeError):
    def __init__(self, limit, allowed, found):
        super().__init__(limit)
        self.limit = limit
        self.allowed = allowed
        self.found = found


def check_source_limit(name, found, allowed):
    if found > allowed:
        raise ExactSourceLimit(name, allowed, found)
