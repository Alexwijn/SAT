from ..manufacturer import Manufacturer


class Intergas(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Intergas'
