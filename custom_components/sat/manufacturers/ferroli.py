from ..manufacturer import Manufacturer


class Ferroli(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Ferroli'
