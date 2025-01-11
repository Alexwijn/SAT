from ..manufacturer import Manufacturer


class ATAG(Manufacturer):
    @property
    def name(self) -> str:
        return 'ATAG'
