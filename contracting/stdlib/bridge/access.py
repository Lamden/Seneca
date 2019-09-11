from ...execution.runtime import rt
from contextlib import ContextDecorator
from types import ModuleType
from ...db.driver import ContractDriver
from collections import deque

ctx = ModuleType('context')


class __export(ContextDecorator):
    def __init__(self, contract):
        self.contract = contract

    def __enter__(self):
        driver = rt.env.get('__Driver') or ContractDriver()

        ctx.owner = driver.get_owner(self.contract)

        print('entering {}'.format(self.contract))
        rt.ctx2.push(self.contract)

        if rt.ctx2.last_parent() == self.contract:
            ctx.caller = rt.signer
        else:
            ctx.caller = rt.ctx2.last_parent()

        ctx.this = self.contract
        ctx.signer = rt.signer

    def __exit__(self, *args, **kwargs):
        print('popping from {}'.format(self.contract))
        rt.ctx2.pop()


exports = {
    '__export': __export,
    'ctx2': ctx,
    'rt': rt,
}