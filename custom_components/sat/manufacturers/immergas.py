from ..manufacturer import Manufacturer


class Immergas(Manufacturer):
    @property
    def identifier(self) -> int:
        return 27

    @property
    def name(self) -> str:
        return 'Immergas'
