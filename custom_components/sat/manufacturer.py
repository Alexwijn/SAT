from abc import abstractmethod

from typing import List, Optional

MANUFACTURERS = {
    "ATAG": "atag",
    "Baxi": "baxi",
    "Brotge": "brotge",
    "DeDietrich": "dedietrich",
    "Ferroli": "ferroli",
    "Geminox": "geminox",
    "Ideal": "ideal",
    "Immergas": "immergas",
    "Intergas": "intergas",
    "Itho": "itho",
    "Nefit": "nefit",
    "Radiant": "radiant",
    "Remeha": "remeha",
    "Sime": "sime",
    "Vaillant": "vaillant",
    "Viessmann": "viessmann",
    "Worcester": "worcester",
    "Other": "other",
}


class Manufacturer:
    @property
    @abstractmethod
    def identifier(self) -> int:
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass


class ManufacturerFactory:
    @staticmethod
    def resolve_by_name(name: str) -> Optional[Manufacturer]:
        """Resolve a Manufacturer instance by its name."""
        if not (module := MANUFACTURERS.get(name)):
            return None

        return ManufacturerFactory._import_class(module, name)()

    @staticmethod
    def resolve_by_member_id(member_id: int) -> List[Manufacturer]:
        """Resolve a list of Manufacturer instances by member ID."""
        return [
            manufacturer
            for name, module in MANUFACTURERS.items()
            if (manufacturer := ManufacturerFactory._import_class(module, name)()).identifier == member_id
        ]

    @staticmethod
    def _import_class(module_name: str, class_name: str):
        """Dynamically import and return a Manufacturer class."""
        return getattr(__import__(f"custom_components.sat.manufacturers.{module_name}", fromlist=[class_name]), class_name)
