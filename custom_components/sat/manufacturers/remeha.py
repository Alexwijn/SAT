from ..manufacturer import Manufacturer


class Remeha(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Remeha'
