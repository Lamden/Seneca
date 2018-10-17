import sys, os, inspect, imp
import encodings.idna
from os.path import join, exists, isdir
from importlib.abc import Loader, MetaPathFinder
from importlib.util import spec_from_file_location
from seneca.engine.interpreter import SenecaInterpreter

class SenecaFinder(MetaPathFinder):

    def find_spec(self, fullname, path, target=None):
        if path is None or path == "":
            path = [os.getcwd()] # top level import --
        if "." in fullname:
            *parents, name = fullname.split(".")
        else:
            name = fullname
        for entry in path:
            if isdir(join(entry, name)):
                # this module has child modules
                filename = join(entry, name, "__init__.py")
                if not exists(filename): open(filename, "w+")
                submodule_locations = [join(entry, name)]
            else:
                filename = join(entry, name + ".sen.py")
                submodule_locations = None
            if not exists(filename):
                continue

            return spec_from_file_location(fullname, filename, loader=SenecaLoader(filename),
                submodule_search_locations=submodule_locations)

        return None # we don't know how to import this

class SenecaLoader(Loader):

    def __init__(self, filename):
        self.filename = filename

    def exec_module(self, module):
        with open(self.filename) as f:
            code_str = f.read()
            tree = SenecaInterpreter.parse_ast(code_str)
            code_obj = compile(tree, filename=self.filename, mode="exec")
            SenecaInterpreter.execute(
                code_obj, vars(module)
            )
        return module

class RedisFinder:

    def find_module(self, fullname, path=None):
        if fullname.startswith('seneca.contracts'):
            return RedisLoader()
        return None

class RedisLoader:

    def load_module(self, fullname):
        module_name = fullname.split('.')[-1]
        code = SenecaInterpreter.get_code_obj(module_name)
        mod = sys.modules.setdefault(fullname, imp.new_module(fullname))
        mod.__file__ = "<%s>" % self.__class__.__name__
        mod.__loader__ = self
        mod.__path__ = []
        mod.__package__ = fullname
        SenecaInterpreter.execute(
            code, mod.__dict__
        )
        return mod