from ..manufacturer import Manufacturer


class Sime(Manufacturer):
    @property
    def identifier(self) -> int:
        return 27

    @property
    def name(self) -> str:
        return 'Sime'
