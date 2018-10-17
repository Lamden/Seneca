from seneca.test_contracts.good import one_you_can_export as good
from seneca.test_contracts.okay import one_you_can_export as okay

@export
def good_call():
    good()
    okay()

@export
def reasonable_call():
    good()

def secret_call():
    okay()