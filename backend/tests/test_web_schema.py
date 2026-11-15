import pytest
from pydantic import ValidationError

from app.core.schemas import WebModel


class ExampleRequest(WebModel):
    name: str
    enabled: bool


def test_valid_input():
    data = ExampleRequest(name="Node trong nhà", enabled=True)

    assert data.name == "Node trong nhà"
    assert data.enabled is True


@pytest.mark.parametrize(
    "payload",
    [
        {"name": 123, "enabled": True},  # Sai kiểu name
        {"name": "Node", "enabled": "true"},  # Sai kiểu enabled
        {"name": "Node", "enabled": 1},  # Số không thay bool
        {"name": "Node"},  # Thiếu enabled
        {"name": "Node", "enabled": True, "abc": 1},  # Thừa trường
    ],
)
def test_invalid_input(payload):
    with pytest.raises(ValidationError):
        ExampleRequest.model_validate(payload)
