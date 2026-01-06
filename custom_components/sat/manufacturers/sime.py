from ..manufacturer import Manufacturer


class Sime(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Sime'
