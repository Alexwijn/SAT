"""Long-term statistics metadata."""

from typing import Optional

from homeassistant.components.recorder.models import StatisticMetaData

try:
    from homeassistant.components.recorder.models import StatisticMeanType
except ImportError:
    StatisticMeanType = None


def statistic_metadata(statistic_id: str, unit: Optional[str], unit_class: Optional[str], has_sum: bool = False) -> StatisticMetaData:
    """Return the metadata of a statistic."""
    metadata = {"has_sum": has_sum, "name": None, "source": "recorder", "statistic_id": statistic_id, "unit_of_measurement": unit}

    if StatisticMeanType is None:
        metadata["has_mean"] = not has_sum
    else:
        metadata["mean_type"] = StatisticMeanType.NONE if has_sum else StatisticMeanType.ARITHMETIC

    if "unit_class" in StatisticMetaData.__annotations__:
        metadata["unit_class"] = unit_class

    return metadata
