from ..manufacturer import Manufacturer


class Geminox(Manufacturer):
    @property
    def identifier(self) -> int:
        return 4

    @property
    def name(self) -> str:
        return 'Geminox'
