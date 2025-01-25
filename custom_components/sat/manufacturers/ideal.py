from ..manufacturer import Manufacturer


class Ideal(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Ideal'
