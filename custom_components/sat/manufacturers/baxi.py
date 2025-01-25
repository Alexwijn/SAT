from ..manufacturer import Manufacturer


class Baxi(Manufacturer):
    @property
    def identifier(self) -> int:
        return 4

    @property
    def name(self) -> str:
        return 'Baxi'
