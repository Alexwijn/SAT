from ..manufacturer import Manufacturer


class Viessmann(Manufacturer):
    @property
    def name(self) -> str:
        return 'Viessmann'
