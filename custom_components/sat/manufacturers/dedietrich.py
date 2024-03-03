from custom_components.sat.manufacturer import Manufacturer


class DeDietrich(Manufacturer):
    @property
    def name(self) -> str:
        return 'De Dietrich'
