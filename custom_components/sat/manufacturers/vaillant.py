from ..manufacturer import Manufacturer


class Vaillant(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Vaillant'
