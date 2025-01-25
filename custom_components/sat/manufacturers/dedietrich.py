from ..manufacturer import Manufacturer


class DeDietrich(Manufacturer):
    @property
    def identifier(self) -> int:
        return 4

    @property
    def name(self) -> str:
        return 'De Dietrich'
