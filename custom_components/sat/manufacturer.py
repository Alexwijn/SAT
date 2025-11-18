from abc import ABC, abstractmethod
from typing import Optional, List, Type

from .helpers import snake_case

MANUFACTURERS = {
    "Atag": 4,
    "Baxi": 4,
    "Brotge": 4,
    "DeDietrich": 4,
    "Ferroli": 9,
    "Geminox": 4,
    "Ideal": 6,
    "Immergas": 27,
    "Intergas": 173,
    "Itho": 29,
    "Nefit": 131,
    "Radiant": 41,
    "Remeha": 11,
    "Sime": 27,
    "Vaillant": 24,
    "Viessmann": 33,
    "Worcester": 95,
    "Other": -1,
}


class Manufacturer(ABC):
    def __init__(self):
        self._member_id = MANUFACTURERS.get(type(self).__name__)

    @property
    def member_id(self) -> int:
        return self._member_id

    @property
    @abstractmethod
    def friendly_name(self) -> str:
        pass


class ManufacturerFactory:
    @staticmethod
    def resolve_by_name(name: str) -> Optional[Manufacturer]:
        """Resolve a Manufacturer instance by its name."""
        if name not in MANUFACTURERS:
            return None

        return ManufacturerFactory._import_class(snake_case(name), name)()

    @staticmethod
    def resolve_by_member_id(member_id: int) -> List[Manufacturer]:
        """Resolve a list of Manufacturer instances by member ID."""
        return [
            ManufacturerFactory._import_class(snake_case(name), name)()
            for name, value in MANUFACTURERS.items()
            if member_id == value
        ]

    @staticmethod
    def _import_class(module_name: str, class_name: str) -> Type[Manufacturer]:
        """Dynamically import and return a Manufacturer class."""
        return getattr(__import__(f"custom_components.sat.manufacturers.{module_name}", fromlist=[class_name]), class_name)
