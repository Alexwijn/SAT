from ..manufacturer import Manufacturer


class Remeha(Manufacturer):
    @property
    def identifier(self) -> int:
        return 11

    @property
    def name(self) -> str:
        return 'Remeha'
