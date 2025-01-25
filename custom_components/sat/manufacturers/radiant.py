from ..manufacturer import Manufacturer


class Radiant(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Radiant'
