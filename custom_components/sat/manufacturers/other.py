from ..manufacturer import Manufacturer


class Other(Manufacturer):
    @property
    def identifier(self) -> int:
        return -1

    @property
    def name(self) -> str:
        return 'Other'
