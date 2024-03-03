from custom_components.sat.manufacturer import Manufacturer


class Ideal(Manufacturer):
    @property
    def name(self) -> str:
        return 'Ideal'
