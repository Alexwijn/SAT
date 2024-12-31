from ..manufacturer import Manufacturer


class Intergas(Manufacturer):
    @property
    def name(self) -> str:
        return 'Intergas'
