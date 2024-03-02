from custom_components.sat.manufacturer import Manufacturer


class Ferroli(Manufacturer):
    @property
    def name(self) -> str:
        return 'Ferroli'
