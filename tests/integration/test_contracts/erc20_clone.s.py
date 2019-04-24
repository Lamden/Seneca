supply = Variable()
balances = Hash(default_value=0)

@seneca_construct
def seed():
    balances['stu'] = 1000000
    balances['colin'] = 100
    supply.set(balances['stu'] + balances['colin'])

@seneca_export
def transfer(amount, to):
    sender = ctx.signer
    assert balances[sender] >= amount, 'Not enough coins to send!'

    balances[sender] -= amount
    balances[to] += amount

@seneca_export
def balance_of(account):
    return balances[account]

@seneca_export
def total_supply():
    return supply.get()

@seneca_export
def allowance(owner, spender):
    return balances[owner, spender]

@seneca_export
def approve(amount, to):
    sender = ctx.signer
    balances[sender, to] += amount
    return balances[sender, to]

@seneca_export
def transfer_from(amount, to, main_account):
    sender = ctx.signer

    assert balances[main_account, sender] >= amount, 'Not enough coins approved to send! You have {} and are trying to spend {}'\
        .format(balances[main_account, sender], amount)
    assert balances[main_account] >= amount, 'Not enough coins to send!'

    balances[main_account, sender] -= amount
    balances[main_account] -= amount

    balances[to] += amount