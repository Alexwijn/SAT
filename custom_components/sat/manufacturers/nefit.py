from ..manufacturer import Manufacturer


class Nefit(Manufacturer):
    @property
    def name(self) -> str:
        return 'Nefit'
