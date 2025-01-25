from ..manufacturer import Manufacturer


class ATAG(Manufacturer):
    @property
    def identifier(self) -> int:
        return 4

    @property
    def name(self) -> str:
        return 'ATAG'
