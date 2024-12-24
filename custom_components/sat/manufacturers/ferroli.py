from ..manufacturer import Manufacturer


class Ferroli(Manufacturer):
    @property
    def name(self) -> str:
        return 'Ferroli'
