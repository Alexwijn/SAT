from ..manufacturer import Manufacturer


class Geminox(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Geminox'
