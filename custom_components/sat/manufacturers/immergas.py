from ..manufacturer import Manufacturer


class Immergas(Manufacturer):
    @property
    def name(self) -> str:
        return 'Immergas'
