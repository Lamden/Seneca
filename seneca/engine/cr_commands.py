import redis
from seneca.libs.logger import get_logger
from seneca.engine.conflict_resolution import CRDataGetSet, CRDataContainer, CRDataBase


class CRCmdMeta(type):
    def __new__(cls, clsname, bases, clsdict):
        clsobj = super().__new__(cls, clsname, bases, clsdict)
        if not hasattr(clsobj, 'registry'):
            clsobj.registry = {}

        if 'COMMAND_NAME' in clsdict:
            # assert 'COMMAND_NAME' in clsdict, 'COMMAND_NAME not set in CRCommand subclass {}'.format(clsname)
            cmd_name = clsdict['COMMAND_NAME']
            assert cmd_name not in clsobj.registry, "Command {} already in registry {}".format(cmd_name, clsobj.registry)

            clsobj.registry[cmd_name] = clsobj

        return clsobj


class CRCmdBase(metaclass=CRCmdMeta):
    MODS_LIST_DELIM = '***'

    def __init__(self, working_db: redis.StrictRedis, master_db: redis.StrictRedis, sbb_idx: int, contract_idx: int,
                 data: CRDataContainer, finalize=False):
        self.log = get_logger("{}[sbb_{}][contract_{}]".format(type(self).__name__, sbb_idx, contract_idx))
        self.finalize = finalize
        self.data = data
        self.working, self.master = working_db, master_db
        self.sbb_idx, self.contract_idx = sbb_idx, contract_idx

    def _add_key_to_mod_list(self, key: str):
        self.log.spam("Adding key <{}> to modification list if it does not exist".format(key))
        all_mods = self.data['mods']

        # Append a new set onto the list if a set of modifications does not exist yet for this contract index
        if len(all_mods) <= self.contract_idx:
            self.log.debugv("Pushing a new element onto modification list for key <{}>".format(key))
            all_mods.append({key})  # We use a set here for dat good O(1) lookup, and also so we dont have to worry about duplicate keys

        # Otherwise, we need to append it to current list of modifications for this contract
        else:
            mods = all_mods[self.contract_idx]
            self.log.debugv("Adding mod key <{}> to existing mod set <{}>".format(key, mods))
            mods.add(key)

        # Development sanity check. This should NEVER happen (we should be executing contracts sequentially per sbb).
        # In other words, we should always be adding to the mod list of the latest contract, not a prior one
        # assert len(self.data['mods']) == self.contract_idx + 1, \
        #     "DEVELOPER LOGIC ERROR!!! Contract idx {} does not match modifications list of length {}"\
        #     .format(self.contract_idx, len(self.data['mods']))

    def _copy_og_key_if_not_exists(self, key, *args, **kwargs):
        """
        Copies the key from either master db or common layer (working db) to the sub-block specific layer, if it does
        not exist already
        """
        # If the key already exists, bounce out of this method immediately
        if self.sbb_original_exists(key):
            self.log.debugv("Key <{}> already exists in sub-block specific data, thus not recopying".format(key))
            return

        # First check the common layer for the key
        if self.working.exists(key):
            self.log.debugv("Copying common key <{}> to sb specific data" .format(key))
            self.copy_key_to_sbb_data(self.working, key)

        # Next, check the Master layer for the key
        elif self.master.exists(key):
            self.log.debugv("Copying master key <{}> to sb specific data" .format(key))
            # type(self)._write(self.working, og_key, val)
            self.copy_key_to_sbb_data(self.master, key)

        # Otherwise, if key not found in common or master layer, complain
        else:
            raise Exception("Key <{}> not found in Master or common layer!".format(key))

    def __call__(self, *args, **kwargs):
        raise NotImplementedError()

    def sbb_original_exists(self, key) -> bool:
        """
        Return True if key exists in the sub-block specific data, and False otherwise.
        """
        raise NotImplementedError()

    def copy_key_to_sbb_data(self, db: redis.StrictRedis, key: str):
        """
        Copies 'key' from the specified to the sub-block specific data
        :param db: The DB to copy the key from
        :param key: The name of the key to copy
        """
        raise NotImplementedError()


class CRCmdGetSetBase(CRCmdBase):

    def sbb_original_exists(self, key) -> bool:
        return key in self.data['getset']

    def copy_key_to_sbb_data(self, db: redis.StrictRedis, key: str):
        self.data['getset'][key] = {'og': db.get(key), 'mod': None}


class CRCmdGet(CRCmdGetSetBase):
    COMMAND_NAME = 'get'

    def __call__(self, key):
        self._copy_og_key_if_not_exists(key)

        # First, try and return the local modified key
        mod_val = self.data['getset'][key]['mod']
        if mod_val is not None:
            self.log.debugv("SBB specific MODIFIED key found for key named <{}>".format(key))
            return mod_val

        # Otherwise, default to the local original key
        self.log.debugv("SBB specific ORIGINAL key found for key named <{}>".format(key))
        return self.data['getset'][key]['og']

        # TODO does phase 2 require special logic?


class CRCmdSet(CRCmdGetSetBase):
    COMMAND_NAME = 'set'

    def __call__(self, key, value):
        assert type(value) in (str, bytes), "Attempted to use 'set' with a value that is not str or bytes (val={}). " \
                                            "This is not supported currently.".format(value)
        self._copy_og_key_if_not_exists(key)
        if type(value) is str:
            value = value.encode()

        # TODO does phase 2 require special logic?

        self.log.debugv("Setting SBB specific key <{}> to value {}".format(key, value))
        self.data['getset'][key]['mod'] = value

        self._add_key_to_mod_list(key)
