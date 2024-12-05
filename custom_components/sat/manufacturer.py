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
            from .manufacturers.simulator import Simulator
            return Simulator()

        if member_id == 4:
            from .manufacturers.geminox import Geminox
            return Geminox()

        if member_id == 6:
            from .manufacturers.ideal import Ideal
            return Ideal()

        if member_id == 9:
            from .manufacturers.ferroli import Ferroli
            return Ferroli()

        if member_id == 11:
            from .manufacturers.dedietrich import DeDietrich
            return DeDietrich()

        if member_id == 27:
            from .manufacturers.immergas import Immergas
            return Immergas()

        if member_id == 131:
            from .manufacturers.nefit import Nefit
            return Nefit()

        if member_id == 173:
            from .manufacturers.intergas import Intergas
            return Intergas()

        return None
