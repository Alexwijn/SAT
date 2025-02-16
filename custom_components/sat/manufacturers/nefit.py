from ..manufacturer import Manufacturer


class Nefit(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Nefit'
