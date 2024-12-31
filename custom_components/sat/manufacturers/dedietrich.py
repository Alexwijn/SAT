from ..manufacturer import Manufacturer


class DeDietrich(Manufacturer):
    @property
    def name(self) -> str:
        return 'De Dietrich'
