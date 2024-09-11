from custom_components.sat.manufacturer import Manufacturer


class Geminox(Manufacturer):
    @property
    def name(self) -> str:
        return 'Geminox'
