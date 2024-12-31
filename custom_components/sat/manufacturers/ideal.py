from ..manufacturer import Manufacturer


class Ideal(Manufacturer):
    @property
    def name(self) -> str:
        return 'Ideal'
