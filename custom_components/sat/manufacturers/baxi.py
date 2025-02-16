from ..manufacturer import Manufacturer


class Baxi(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Baxi'
