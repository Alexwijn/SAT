from custom_components.sat.manufacturer import Manufacturer


class Intergas(Manufacturer):
    @property
    def name(self) -> str:
        return 'Intergas'
