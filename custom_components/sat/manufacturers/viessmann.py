from ..manufacturer import Manufacturer


class Viessmann(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Viessmann'
