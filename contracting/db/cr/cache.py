# Builtin imports


# Third party imports
from transitions import Machine
from transitions.extensions.states import add_state_features, Timeout

# Local imports
from contracting.logger import get_logger
from contracting.db.driver import ContractDriver, CacheDriver
from contracting.db.cr.transaction_bag import TransactionBag
from contracting import config
from contracting.db.cr.callback_data import ExecutionData, SBData
from typing import List

import json

# TODO include _key exclusions for stamps, etc
class Macros:
    # TODO we need to make sure these keys dont conflict with user stuff in the common layer. I.e. users cannot be
    # creating keys named '_execution' or '_conflict_resolution'
    EXECUTION = '_execution_phase'
    CONFLICT_RESOLUTION = '_conflict_resolution_phase'
    RESET = "_reset_phase"

    ALL_MACROS = [EXECUTION, CONFLICT_RESOLUTION, RESET]

@add_state_features(Timeout)
class CustomStateMachine(Machine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

# Uncomment this if you want to generate a new visual representation of the state machine
#from transitions.extensions import GraphMachine
#@add_state_features(Timeout)
#class CustomStateMachine(GraphMachine):
#    def __init__(self, *args, **kwargs):
#        kwargs['show_conditions'] = True
#        kwargs['title'] = 'CRCache State Machine'
#        super().__init__(*args, **kwargs)


class CRCache:

    states = [
        {'name': 'CLEAN'},
        {'name': 'BAG_SET'},
        {'name': 'EXECUTED'},
        {'name': 'CR_STARTED'},
        {'name': 'READY_TO_COMMIT'},
        {'name': 'COMMITTED'},
        {'name': 'READY_TO_MERGE'},
        {'name': 'MERGED'},
        {'name': 'DISCARDED'},
        {'name': 'RESET'}
    ]

    def __init__(self, idx, master_db, sbb_idx, num_sbb, executor, scheduler):
        self.idx = idx
        self.sbb_idx = sbb_idx
        self.num_sbb = num_sbb
        self.executor = executor
        self.scheduler = scheduler

        self.bag = None            # Bag will be set by the execute call
        self.rerun_idx = None      # The index to being reruns at
        self.results = {}          # The results of the execution
        self.macros = Macros()     # Instance of the macros class for mutex/sync
        self.input_hash = None     # The 'input hash' of the bag we are executing, a 64 char hex str

        name = self.__class__.__name__ + "[cache-{}]".format(self.idx)
        self.log = get_logger(name)

        self.db = ContractDriver(db=self.idx)
        self.master_db = master_db

        transitions = [
            {
                'trigger': 'set_bag',
                'source': 'CLEAN',
                'dest': 'BAG_SET',
                'before': 'set_transaction_bag',
            },
            {
                'trigger': 'execute',
                'source': 'BAG_SET',
                'dest': 'EXECUTED',
                'before': 'execute_transactions',
                'after': '_schedule_cr'
            },
            { # ASYNC CALL TO MOVE OUT FROM EXECUTED sync_execution
                'trigger': 'sync_execution',
                'source': 'EXECUTED',
                'dest': 'CR_STARTED',
                'conditions': ['my_turn_for_cr', 'is_top_of_stack'],
                'after': 'start_cr'
            },
            {
                'trigger': 'start_cr',
                'source': 'CR_STARTED',
                'dest': 'READY_TO_COMMIT',
                'before': 'resolve_conflicts',
                'after': 'commit'
            },
            {
                'trigger': 'commit',
                'source': 'READY_TO_COMMIT',
                'dest': 'COMMITTED',
                'before': 'merge_to_common',
                'after': '_schedule_merge_ready'
            },
            { # ASYNC CALL FROM OUTSIDE, TIMEOUT HERE TO ERROR
                'trigger': 'sync_merge_ready',
                'source': 'COMMITTED',
                'dest': 'READY_TO_MERGE',
                'conditions': 'all_committed',
            },
            { # WILL WAIT HERE FOR MERGE TO BE CALLED
                'trigger': 'merge',
                'source': 'READY_TO_MERGE',
                'dest': 'MERGED',
                'before': 'merge_to_master',
                'after': 'reset'
            },
            {
                'trigger': 'reset',
                'source': ['MERGED', 'DISCARDED'],
                'dest': 'RESET',
                'before': 'reset_dbs',
                'after': '_schedule_reset'
            },
            {
                'trigger': 'sync_reset',
                'source': 'RESET',
                'dest': 'CLEAN',
                'conditions': 'all_reset',
                'after': '_mark_clean'
            },
            {
                'trigger': 'discard',
                'source': ['BAG_SET', 'EXECUTED', 'CR_STARTED', 'READY_TO_COMMIT', 'COMMITTED', 'READY_TO_MERGE'],
                'dest': 'DISCARDED',
                'after': 'reset'
            }
        ]
        self.machine = CustomStateMachine(model=self, states=CRCache.states,
                                          transitions=transitions, initial='CLEAN')

        self.scheduler.mark_clean(self)
        self._reset_macro_keys()

    def _schedule_cr(self):
        # Add sync_execution to the scheduler to wait for the CR step
        self.scheduler.add_poll(self, self.sync_execution, 'COMMITTED')

    def _schedule_merge_ready(self):
        self.log.important2("scheding merge rdy {}".format(self))
        self.scheduler.add_poll(self, self.sync_merge_ready, 'READY_TO_MERGE')

    def _schedule_reset(self):
        self.scheduler.add_poll(self, self.sync_reset, 'CLEAN')

    def _incr_macro_key(self, macro):
        self.log.debug("INCREMENTING MACRO {}".format(macro))
        self.db.incrby(macro)

    def _check_macro_key(self, macro):
        val = self.db.get_direct(macro)
        # self.log.debug("MACRO: {} VAL: {} VALTYPE: {}".format(macro, val, type(val)))
        return int(val) if val is not None else -1

    def _reset_macro_keys(self):
        self.log.spam("{} is resetting macro keys".format(self))
        for key in Macros.ALL_MACROS:
            self.db.set_direct(key, 0)

    def get_results(self):
        return self.results

    def set_transaction_bag(self, bag):
        self.log.spam("{} is setting transactions!".format(self))
        self.bag = bag
        if self.sbb_idx == 0:
            self._incr_macro_key(Macros.RESET)

    def execute_transactions(self):
        self.log.spam("{} is executing transactions!".format(self))
        # Execute first round using Master DB Driver since we will not have any keys in common
        # Do not commit, leveraging cache only
        self.results = self.executor.execute_bag(self.bag, environment=self.bag.environment, driver=self.master_db)

        # Copy the cache from Master DB Driver to the contained Driver for common
        self.db.reset_cache(modified_keys=self.master_db.modified_keys,
                            contract_modifications=self.master_db.contract_modifications,
                            original_values=self.master_db.original_values)
        # Reset the master_db cache back to empty
        self.master_db.reset_cache()

        # Increment the execution macro
        self._incr_macro_key(Macros.EXECUTION)

    def my_turn_for_cr(self):
        return self._check_macro_key(Macros.CONFLICT_RESOLUTION) == self.sbb_idx

    def is_top_of_stack(self):
        return self.scheduler.check_top_of_stack(self)

    def prepare_reruns(self):
        # Find all instances where our originally grabbed value from the cache does not
        # match the value in the DB, cascade from common to master, if the _key doesn't
        # exist in common, check master since another CRCache may have merged since you
        # executed.
        cr_key_hits = []
        for key, value in self.db.original_values.items():
            if key not in cr_key_hits:
                common_db_value = super(CacheDriver, self.db).get(key)
                if common_db_value is not None:
                    if common_db_value != value:
                        cr_key_hits.append(key)
                else:
                    master_db_value = super(CacheDriver, self.master_db).get(key)
                    if master_db_value != value:
                        cr_key_hits.append(key)

        # Check the modified keys list for the lowest contract index, set that as the
        # rerun index so we can rerun all contracts following the first mismatch
        if len(cr_key_hits) > 0:
            cr_key_modifications = {k: v for k, v in self.db.modified_keys.items() if k in cr_key_hits}
            self.rerun_idx = 999999
            for key, value in cr_key_modifications.items():
                if value[0] < self.rerun_idx:
                    self.rerun_idx = value[0]

    def requires_reruns(self):
        return self.rerun_idx is not None

    def rerun_transactions(self):
        self.db.revert(idx=self.rerun_idx)
        self.bag.yield_from(idx=self.rerun_idx)
        self.results.update(self.executor.execute_bag(self.bag, environment=self.bag.environment, driver=self.db))

    def resolve_conflicts(self):
        self.prepare_reruns()
        if self.requires_reruns():
            self.rerun_transactions()

    def merge_to_common(self):
        # call completion handler on bag so Cilantro can build a SubBlockContender
        self.bag.completion_handler(self._get_sb_data())

        self.db.commit()  # this will wipe the cache
        self._incr_macro_key(Macros.CONFLICT_RESOLUTION)

    def all_committed(self):
        return self._check_macro_key(Macros.CONFLICT_RESOLUTION) == self.num_sbb

    def merge_to_master(self):
        self.log.debugv("merging to master !!")
        if self.sbb_idx == 0:
            merge_keys = [ x for x in self.db.keys() if x not in Macros.ALL_MACROS ]
            for key in merge_keys:
                self.master_db.set(key, self.db.get(key))
            self.master_db.commit()

    def reset_dbs(self):
        # If we are on SBB 0, we need to flush the common layer of this cache
        # since the DB is shared, we only need to call this from one of the SBBs
        self.db.reset_cache()
        self.master_db.reset_cache()
        self.rerun_idx = None
        self.bag = None

        # If we are on SBB 0, we need to flush the common layer of this cache
        # since the DB is shared, we only need to call this from one of the SBBs
        # TODO - this should be a macro so we can switch to other sbbers if needed
        if self.sbb_idx == 0:
            self.log.debugv("cache idx 0 FLUSHING DB!!!!")
            self.db.flush()
            self._reset_macro_keys()

    def all_reset(self):
        return (self._check_macro_key(Macros.RESET) == 0)

    def _mark_clean(self):
        # Mark myself as clean for the FSMScheduler to be able to reuse me
        self.log.info("raghu mark clean this cache!!!!")
        self.scheduler.mark_clean(self)

    def _get_sb_data(self) -> SBData:
        if len(self.results) != len(self.bag.transactions):
            self.log.critical("Mismatch of state: length of results is {} but bag has {} txs. Discarding." \
                              .format(len(self.results), len(self.bag.transactions)))
            self.discard()
            return [] # colin is this necessary?? also what should i return for cilatnro to be aware of the goof?

        tx_datas = []
        i = 0

        # Iterate over results to take into account transactions that have been reverted and removed from contract_mods
        # This is the most evil code written by man
        for tx_idx in sorted(self.results.keys()):

            status_code, result, stamps = self.results[tx_idx]
            state_str = ""

            if status_code == 0:
                mods = self.db.contract_modifications[i]
                i += 1
                state_str = json.dumps(mods)

            tx_datas.append(ExecutionData(contract=self.bag.transactions[tx_idx], status=status_code,
                                          response=result, state=state_str, stamps=stamps))

        return SBData(self.bag.input_hash, tx_data=tx_datas)

    def _get_macro_values(self):
        mv_str = ''
        for key in Macros.ALL_MACROS:
            my_str + = str(self._check_macro_key(key)) + ' '
        return mv_str

    def __repr__(self):
        input_hash = 'NOT_SET' if self.bag is None else self.bag.input_hash
        return "<CRCache input_hash={}, state={}, idx={}, sbb_idx={}, macros={}, top_of_stk={}>"\
               .format(input_hash, self.state, self.idx, self.sbb_idx, self._get_macro_values(), self.is_top_of_stack())


if __name__ == "__main__":
    c = CRCache(1,1,1,1,1)
    c.machine.get_graph().draw('CRCache_StateMachine.png', prog='dot')
