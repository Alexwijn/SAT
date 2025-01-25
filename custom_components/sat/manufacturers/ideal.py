from ..manufacturer import Manufacturer


class Ideal(Manufacturer):
    @property
    def identifier(self) -> int:
        return 6

    @property
    def name(self) -> str:
        return 'Ideal'
