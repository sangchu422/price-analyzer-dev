from app.procurement.families import FAMILY_RULE_VERSION, classify_item_family


def test_family_rules_use_operator_labels_without_ai_prefix() -> None:
    sensor = classify_item_family("PHOTO SENSOR", "E3Z-LS61", "SENSOR_MEASUREMENT")
    motor = classify_item_family("AC SERVO MOTOR", "1KW", "DRIVE_MOTION")
    cable = classify_item_family("ROBOT CABLE", "10M", "CABLE_CONNECTOR")

    assert sensor.code == "SENSOR"
    assert sensor.name == "센서류"
    assert motor.name == "모터류"
    assert cable.name == "케이블·전선류"
    assert "AI" not in sensor.name
    assert FAMILY_RULE_VERSION == "item-family-keyword-v1"


def test_every_category_has_a_family_fallback() -> None:
    categories = (
        "DRIVE_MOTION",
        "SENSOR_MEASUREMENT",
        "ELECTRICAL_CONTROL",
        "PNEUMATIC_HYDRAULIC",
        "MATERIAL_HANDLING",
        "MECHANICAL_FABRICATION",
        "TOOLING_FIXTURE",
        "UTILITY_ENVIRONMENT",
        "CABLE_CONNECTOR",
        "FASTENER_CONSUMABLE",
        "SAFETY",
        "IT_NETWORK",
        "LABOR_SERVICE",
        "GENERAL_COMPONENT",
    )

    matches = [classify_item_family("UNKNOWN PART", None, code) for code in categories]

    assert len({match.code for match in matches}) == len(categories)
    assert all(match.name.endswith("류") for match in matches)
