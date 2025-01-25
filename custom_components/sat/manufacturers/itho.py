from ..manufacturer import Manufacturer


class Itho(Manufacturer):
    @property
    def identifier(self) -> int:
        return 29

    @property
    def name(self) -> str:
        return 'Itho'
