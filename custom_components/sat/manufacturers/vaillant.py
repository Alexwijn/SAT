from ..manufacturer import Manufacturer


class Vaillant(Manufacturer):
    @property
    def identifier(self) -> int:
        return 24

    @property
    def name(self) -> str:
        return 'Vaillant'
