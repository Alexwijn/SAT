from ..manufacturer import Manufacturer


class Immergas(Manufacturer):
    @property
    def friendly_name(self) -> str:
        return 'Immergas'
