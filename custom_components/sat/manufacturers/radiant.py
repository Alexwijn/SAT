from ..manufacturer import Manufacturer


class Radiant(Manufacturer):
    @property
    def identifier(self) -> int:
        return 41

    @property
    def name(self) -> str:
        return 'Radiant'
