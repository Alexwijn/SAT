from ..manufacturer import Manufacturer


class Itho(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Itho'
