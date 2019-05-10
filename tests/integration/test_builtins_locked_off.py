from unittest import TestCase
from contracting.stdlib.bridge.time import Datetime
from contracting.client import ContractingClient
from contracting.execution.executor import Executor

class TestBuiltinsLockedOff(TestCase):
    def setUp(self):
        self.c = ContractingClient(signer='stu', executor=Executor(production=True))
        with open('./test_contracts/builtin_lib.s.py') as f:
            contract = f.read()

        self.c.submit(contract, name='builtin')

    def tearDown(self):
        self.c.raw_driver.flush()
        #self.c.executor.sandbox.terminate()

    def test_if_builtin_can_be_called(self):
        builtin = self.c.get_contract('builtin')
        print(builtin.return_token())
