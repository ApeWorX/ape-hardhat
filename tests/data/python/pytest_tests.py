def test_provider(networks):
    # The default gets set in `ape-config.yaml`
    assert networks.provider.name == "hardhat"


def test_contract_interaction(owner, contract):
    contract.setNumber(123, sender=owner)
    assert contract.myNumber() == 123


def test_transfer(accounts):
    # Useful for seeing transfer gas
    accounts[0].transfer(accounts[1], "100 gwei")


def test_using_contract_with_same_name(owner, project):
    contract = project.TestContractVy.deploy(sender=owner)
    contract.setNumber(123, sender=owner)
    assert contract.myNumber() == 123


def test_two_contracts_with_same_symbol(owner, accounts, project):
    # Tests against scenario when using 2 tokens with same symbol.
    # There was almost a bug where the contract IDs clashed.
    # This is to help prevent future bugs related to this.

    receiver = accounts[1]
    token_a = project.TokenA.deploy(sender=owner)
    token_b = project.TokenB.deploy(sender=owner)
    token_a.transfer(receiver, 5, sender=owner)
    token_b.transfer(receiver, 6, sender=owner)
    assert token_a.balanceOf(receiver) == 5
    assert token_b.balanceOf(receiver) == 6
