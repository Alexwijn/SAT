import sys
from types import SimpleNamespace
from types import ModuleType

from homeassistant.components import number, sensor

esphome = ModuleType("homeassistant.components.esphome")
esphome.DOMAIN = "esphome"
sys.modules["homeassistant.components.esphome"] = esphome

from custom_components.sat.esphome import SatEspHomeCoordinator


class EntityRegistry:
    def __init__(
        self,
        entity_id: str | None = None,
        domain: str = number.DOMAIN,
        platform: str = esphome.DOMAIN,
        unique_id: str = "94:E6:86:3C:EE:6C-number-t_set",
    ) -> None:
        self.entity_id = entity_id
        self.domain = domain
        self.platform = platform
        self.unique_id = unique_id

    def async_get_entity_id(self, domain: str, platform: str, unique_id: str) -> str | None:
        if domain == self.domain and platform == self.platform and unique_id == self.unique_id:
            return self.entity_id

        return None


def create_coordinator(entity_registry: EntityRegistry, entities: list[SimpleNamespace]) -> SatEspHomeCoordinator:
    coordinator = SatEspHomeCoordinator.__new__(SatEspHomeCoordinator)
    coordinator._mac_address = "94:E6:86:3C:EE:6C"
    coordinator._entity_registry = entity_registry
    coordinator._entities = entities
    return coordinator


def test_get_entity_id_supports_legacy_esphome_unique_id() -> None:
    coordinator = create_coordinator(EntityRegistry("number.opentherm_t_set"), [])

    assert coordinator._get_entity_id(number.DOMAIN, "t_set") == "number.opentherm_t_set"


def test_get_entity_id_supports_current_esphome_unique_id() -> None:
    coordinator = create_coordinator(
        EntityRegistry(),
        [
            SimpleNamespace(
                platform=esphome.DOMAIN,
                domain=number.DOMAIN,
                unique_id="94:E6:86:3C:EE:6C/0/number/t_set",
                entity_id="number.lavanderia_opentherm_t_set",
            ),
        ],
    )

    assert coordinator._get_entity_id(number.DOMAIN, "t_set") == "number.lavanderia_opentherm_t_set"


def test_get_entity_id_prefers_current_esphome_unique_id() -> None:
    coordinator = create_coordinator(
        EntityRegistry("number.legacy_opentherm_t_set"),
        [
            SimpleNamespace(
                platform=esphome.DOMAIN,
                domain=number.DOMAIN,
                unique_id="94:E6:86:3C:EE:6C/0/number/t_set",
                entity_id="number.current_opentherm_t_set",
            ),
        ],
    )

    assert coordinator._get_entity_id(number.DOMAIN, "t_set") == "number.current_opentherm_t_set"


def test_get_entity_id_ignores_other_domains_for_current_esphome_unique_id() -> None:
    coordinator = create_coordinator(
        EntityRegistry(),
        [
            SimpleNamespace(
                platform=esphome.DOMAIN,
                domain=sensor.DOMAIN,
                unique_id="94:E6:86:3C:EE:6C/0/sensor/t_set",
                entity_id="sensor.lavanderia_opentherm_t_set",
            ),
        ],
    )

    assert coordinator._get_entity_id(number.DOMAIN, "t_set") is None


def test_get_entity_id_ignores_other_platforms_for_current_esphome_unique_id() -> None:
    coordinator = create_coordinator(
        EntityRegistry(),
        [
            SimpleNamespace(
                platform="mqtt",
                domain=number.DOMAIN,
                unique_id="94:E6:86:3C:EE:6C/0/number/t_set",
                entity_id="number.lavanderia_opentherm_t_set",
            ),
        ],
    )

    assert coordinator._get_entity_id(number.DOMAIN, "t_set") is None


def test_get_entity_id_ignores_malformed_current_esphome_unique_id_and_uses_legacy_fallback() -> None:
    coordinator = create_coordinator(
        EntityRegistry("number.legacy_opentherm_t_set"),
        [
            SimpleNamespace(
                platform=esphome.DOMAIN,
                domain=number.DOMAIN,
                unique_id="94:E6:86:3C:EE:6C/0/number/opentherm/t_set",
                entity_id="number.lavanderia_opentherm_t_set",
            ),
        ],
    )

    assert coordinator._get_entity_id(number.DOMAIN, "t_set") == "number.legacy_opentherm_t_set"
