from custom_components.sat.manufacturer import Manufacturer


class Immergas(Manufacturer):
    @property
    def name(self) -> str:
        return 'Immergas'
