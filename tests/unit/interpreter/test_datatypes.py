from unittest import TestCase
import redis, unittest, seneca
from seneca.constants.config import MASTER_DB, REDIS_PORT
from seneca.engine.storage.datatypes import Map, Table, SchemaArgs
from seneca.engine.interpret.parser import Parser
from decimal import Decimal


class Executor:
    def __init__(self):
        self.r = redis.StrictRedis(host='localhost', port=REDIS_PORT, db=MASTER_DB)
        self.r.flushall()
        Parser.executor = self
        Parser.parser_scope = {
            'rt': {
                'contract': 'sample',
                'sender': 'falcon',
                'author': '__lamden_io__'
            }
        }


class TestDataTypes(TestCase):

    def setUp(self):
        self.ex = Executor()
        Parser.parser_scope['rt']['contract'] = self.id()
        print('#'*128)
        print('\t', self.id())
        print('#'*128)

    def test_map(self):
        balances = Map('balances')
        balances['hr'] = Map('hr')
        self.assertEqual(repr(balances['hr']), 'Map:{}:balances:hr'.format(self.id()))

    def test_map_nested(self):
        balances = Map('balances')
        hooter = Map('hoot')
        hooter['res'] = 1234
        balances['hr'] = Map('hr')
        balances['hr']['hey'] = hooter
        self.assertEqual(balances['hr']['hey']['res'], 1234)

    def test_map_nested_different_type(self):
        Coin = Table('Coin', {
            'name': SchemaArgs(str, required=True),
            'purpose': str
        })
        tau = Coin.add_row('tau', 'something')
        balances = Map('balances')
        balances['hr'] = Map('hr')
        balances['hr']['hey'] = tau
        self.assertEqual(balances['hr']['hey'].schema, Coin.schema)

    def test_table_append(self):
        Coin = Table('Coin', {
            'name': SchemaArgs(str, required=True),
            'purpose': str,
            'price': int
        })
        Coin.add_row('tau', purpose='anarchy net')
        Coin.add_row(purpose='anarchy net', name='stubucks', price=1)
        Coin.add_row('falcoin', 'anarchy net')

        self.assertEqual(Coin.count(), 3)

    def test_table_indexed(self):
        Coin = Table('Coin', {
            'name': SchemaArgs(str, required=True, indexed=True),
            'purpose': str,
            'price': int
        })
        Coin.add_row('faltau', purpose='anarchy net')
        Coin.add_row(purpose='anarchy net', name='stubucks', price=1)
        Coin.add_row('falcoin', 'anarchy net')
        self.assertEqual(Coin.find(field='name', exactly='faltau'), [('faltau', 'anarchy net', Decimal('0'))])
        self.assertEqual(Coin.find(field='name', matches='fal*'), [('faltau', 'anarchy net', Decimal('0')), ('falcoin', 'anarchy net', Decimal('0'))])

    def test_table_find_first_last_start_stop(self):
        Coin = Table('Coin', {
            'name': SchemaArgs(str, required=True),
            'purpose': str,
            'price': int
        })
        Coin.add_row('tau', purpose='anarchy net')
        Coin.add_row(purpose='anarchy net', name='stubucks', price=1)
        Coin.add_row('falcoin', 'anarchy net')

        self.assertEqual([('tau', 'anarchy net', Decimal('0')), ('stubucks', 'anarchy net', Decimal('1'))],
                         Coin.find(first=2))
        self.assertEqual([('stubucks', 'anarchy net', Decimal('1')), ('falcoin', 'anarchy net', Decimal('0'))],
                         Coin.find(last=2))
        self.assertEqual([('stubucks', 'anarchy net', Decimal('1')), ('falcoin', 'anarchy net', Decimal('0'))],
                         Coin.find(start_idx=1, stop_idx=2))

    def test_table_with_table_as_type(self):
        Coin = Table('Coin', {
            'name': SchemaArgs(str, required=True),
            'purpose': SchemaArgs(str, default='anarchy net')
        })
        Company = Table('Company', {
            'name': str,
            'coin': Coin,
            'evaluation': int
        })
        tau = Coin.add_row('tau')
        lamden = Company.add_row('lamden', coin=tau, evaluation=0)
        self.assertEqual(repr(tau), 'Table:{}:Coin'.format(self.id()))
        self.assertEqual(repr(lamden), 'Table:{}:Company'.format(self.id()))

    def test_table_with_invalid_table_type(self):
        Coin = Table('Coin', {
            'name': SchemaArgs(str, True),
            'purpose': SchemaArgs(str, False, '')
        })
        Fake = Table('Fake', {
            'name': SchemaArgs(str, True),
            'purpose': SchemaArgs(str, False, '')
        })
        Company = Table('Company', {
            'name': SchemaArgs(str),
            'coin': SchemaArgs(Coin),
            'evaluation': SchemaArgs(int)
        })
        fake_tau = Fake.add_row('tau', 'anarchy net')
        with self.assertRaises(AssertionError) as context:
            lamden = Company.add_row('lamden', coin=fake_tau, evaluation=0)

    def test_table_delete(self):
        Coin = Table('Coin', {
            'name': SchemaArgs(str, required=True, indexed=True),
            'purpose': str,
            'price': int
        })
        Coin.add_row('faltau', purpose='anarchy net')
        Coin.add_row(purpose='anarchy net', name='stubucks', price=1)
        Coin.add_row('falcoin', 'anarchy net')
        Coin.delete_table()
        self.assertEqual(self.ex.r.keys(), [])

if __name__ == '__main__':
    unittest.main()
