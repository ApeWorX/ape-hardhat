import pytest
from ape.exceptions import SignatureError


def test_send_transaction(contract_instance, owner, hardhat_connected):
    contract_instance.setNumber(10, sender=owner)
    assert contract_instance.myNumber() == 10

    # Have to be in the same test because of X-dist complications
    with pytest.raises(SignatureError):
        contract_instance.setNumber(20)
