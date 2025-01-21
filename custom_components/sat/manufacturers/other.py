from ..manufacturer import Manufacturer


class Other(Manufacturer):
    @property
    def name(self) -> str:
        return 'Other'
