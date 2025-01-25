from ..manufacturer import Manufacturer


class Worcester(Manufacturer):
    @property
    def identifier(self) -> int:
        return 4

    @property
    def name(self) -> str:
        return 'Worcester Bosch'
