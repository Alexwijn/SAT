from ..manufacturer import Manufacturer


class DeDietrich(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'De Dietrich'
