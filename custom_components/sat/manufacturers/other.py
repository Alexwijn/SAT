from ..manufacturer import Manufacturer


class Other(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Other'
