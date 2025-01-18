from ..manufacturer import Manufacturer


class Sime(Manufacturer):
    @property
    def name(self) -> str:
        return 'Sime'
