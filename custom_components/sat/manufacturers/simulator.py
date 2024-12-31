from ..manufacturer import Manufacturer


class Simulator(Manufacturer):
    @property
    def name(self) -> str:
        return 'Simulator'
