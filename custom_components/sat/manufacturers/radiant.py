from ..manufacturer import Manufacturer


class Baxi(Manufacturer):
    @property
    def name(self) -> str:
        return 'Radiant'
