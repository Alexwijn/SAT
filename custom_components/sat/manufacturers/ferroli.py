from ..manufacturer import Manufacturer


class Ferroli(Manufacturer):
    @property
    def identifier(self) -> int:
        return 9

    @property
    def name(self) -> str:
        return 'Ferroli'
