import pytest

def calculate_total(price, quantity):
    if price <= 0 or quantity <= 0:
        raise ValueError("Үнэ болон тоо хэмжээ зөв байх ёстой")
    return price * quantity
def test_normal_order():
    assert calculate_total(15000, 2) == 30000
def test_single_order():
    assert calculate_total(12000, 1) == 12000

def test_invalid_quantity():
    with pytest.raises(ValueError):
        calculate_total(15000, 0)

def test_invalid_price():
    with pytest.raises(ValueError):
        calculate_total(-5000, 2)