from custom_components.sat.manufacturer import MANUFACTURERS, ManufacturerFactory


def test_resolve_by_name():
    """Test resolving manufacturers by name."""
    for name, data in MANUFACTURERS.items():
        # Test valid name
        manufacturer = ManufacturerFactory.resolve_by_name(name)
        assert manufacturer is not None, f"Manufacturer '{name}' should not be None"
        assert manufacturer.__class__.__name__ == name

    # Test invalid name
    manufacturer = ManufacturerFactory.resolve_by_name("InvalidName")
    assert manufacturer is None, "resolve_by_name should return None for invalid names"


def test_resolve_by_member_id():
    """Test resolving manufacturers by member ID."""
    member_id_to_names = {member_id: [] for name, member_id in MANUFACTURERS.items()}
    for name, member_id in MANUFACTURERS.items():
        member_id_to_names[member_id].append(name)

    for member_id, names in member_id_to_names.items():
        manufacturers = ManufacturerFactory.resolve_by_member_id(member_id)
        assert len(manufacturers) == len(names), f"Expected {len(names)} manufacturers for member ID {member_id}"

        for manufacturer in manufacturers:
            assert manufacturer.__class__.__name__ in names, f"Manufacturer name '{manufacturer.name}' not expected for member ID {member_id}"

    # Test invalid member ID
    manufacturers = ManufacturerFactory.resolve_by_member_id(999)
    assert manufacturers == [], "resolve_by_member_id should return an empty list for invalid member IDs"
