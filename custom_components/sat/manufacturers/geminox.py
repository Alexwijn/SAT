from ..manufacturer import Manufacturer


class Geminox(Manufacturer):
    @property
    def name(self) -> str:
        return 'Siemens Group Board ( ATAG, BAXI, BRÖTGE, ELCO THISION, GEMINOX )'
