from ..manufacturer import Manufacturer


class Intergas(Manufacturer):
    @property
    def identifier(self) -> int:
        return 173

    @property
    def name(self) -> str:
        return 'Intergas'
