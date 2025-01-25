from ..manufacturer import Manufacturer


class Atag(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'ATAG'
