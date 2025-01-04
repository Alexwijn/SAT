from abc import abstractmethod

from typing import List, Union

MANUFACTURERS = {
    "Simulator": {"module": "simulator", "class": "Simulator", "id": -1},
    "ATAG": {"module": "atag", "class": "ATAG", "id": 4},
    "Baxi": {"module": "baxi", "class": "Baxi", "id": 4},
    "Brotge": {"module": "brotge", "class": "Brotge", "id": 4},
    "Geminox": {"module": "geminox", "class": "Geminox", "id": 4},
    "Ideal": {"module": "ideal", "class": "Ideal", "id": 6},
    "Ferroli": {"module": "ferroli", "class": "Ferroli", "id": 9},
    "DeDietrich": {"module": "dedietrich", "class": "DeDietrich", "id": 11},
    "Immergas": {"module": "immergas", "class": "Immergas", "id": 27},
    "Sime": {"module": "sime", "class": "Sime", "id": 27},
    "Nefit": {"module": "nefit", "class": "Nefit", "id": 131},
    "Intergas": {"module": "intergas", "class": "Intergas", "id": 173},
}


class Manufacturer:
    @property
    @abstractmethod
    def name(self) -> str:
        pass


class ManufacturerFactory:
    @staticmethod
    def resolve_by_name(name: str) -> Union[Manufacturer, None]:
        """Resolve a Manufacturer instance by its name."""
        manufacturer = MANUFACTURERS.get(name)
        if not manufacturer:
            return None

        return ManufacturerFactory._import_class(manufacturer["module"], manufacturer["class"])()

    @staticmethod
    def resolve_by_member_id(member_id: int) -> List[Manufacturer]:
        """Resolve a list of Manufacturer instances by member ID."""
        return [
            ManufacturerFactory._import_class(info["module"], info["class"])()
            for name, info in MANUFACTURERS.items()
            if info["id"] == member_id
        ]

    @staticmethod
    def _import_class(module_name: str, class_name: str):
        """Dynamically import and return a Manufacturer class."""
        module = __import__(f"custom_components.sat.manufacturers.{module_name}", fromlist=[class_name])
        return getattr(module, class_name)
