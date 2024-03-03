from abc import abstractmethod


class Manufacturer:
    @property
    @abstractmethod
    def name(self) -> str:
        pass


class ManufacturerFactory:
    @abstractmethod
    def resolve(self, member_id: int) -> Manufacturer | None:
        if member_id == -1:
            from custom_components.sat.manufacturers.simulator import Simulator
            return Simulator()

        if member_id == 6:
            from custom_components.sat.manufacturers.ideal import Ideal
            return Ideal()

        if member_id == 11:
            from custom_components.sat.manufacturers.dedietrich import DeDietrich
            return DeDietrich()

        if member_id == 27:
            from custom_components.sat.manufacturers.immergas import Immergas
            return Immergas()

        if member_id == 131:
            from custom_components.sat.manufacturers.nefit import Nefit
            return Nefit()

        if member_id == 173:
            from custom_components.sat.manufacturers.intergas  import Intergas
            return Intergas()

        return None
