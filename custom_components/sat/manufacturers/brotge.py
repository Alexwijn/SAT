from ..manufacturer import Manufacturer


class Brotge(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'BRÖTGE'
