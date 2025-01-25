from ..manufacturer import Manufacturer


class Nefit(Manufacturer):
    @property
    def identifier(self) -> int:
        return 131

    @property
    def name(self) -> str:
        return 'Nefit'
