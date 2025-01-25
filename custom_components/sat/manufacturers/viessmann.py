from ..manufacturer import Manufacturer


class Viessmann(Manufacturer):
    @property
    def identifier(self) -> int:
        return 33

    @property
    def name(self) -> str:
        return 'Viessmann'
