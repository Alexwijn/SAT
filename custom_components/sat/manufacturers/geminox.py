from ..manufacturer import Manufacturer


class Geminox(Manufacturer):
    @property
    def name(self) -> str:
        return 'ATAG/BAXI/BRÖTGE/ELCO/GEMINOX'
