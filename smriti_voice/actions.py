from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
class Action(str,Enum):
    OPEN_PLAY='OPEN_PLAY'; OPEN_MY_PEOPLE='OPEN_MY_PEOPLE'; OPEN_TODAY='OPEN_TODAY'; CALL_PRIMARY_CONTACT='CALL_PRIMARY_CONTACT'; CALL_BINA='CALL_BINA'; OPEN_MEDICINE='OPEN_MEDICINE'; HELP='HELP'; STOP='STOP'; CREATE_REMINDER='CREATE_REMINDER'; START_GAME='START_GAME'; NO_ACTION='NO_ACTION'
SAFE={Action.OPEN_PLAY,Action.OPEN_MY_PEOPLE,Action.OPEN_TODAY,Action.CALL_PRIMARY_CONTACT,Action.CALL_BINA,Action.OPEN_MEDICINE,Action.HELP,Action.STOP,Action.CREATE_REMINDER,Action.START_GAME}
@dataclass(frozen=True)
class ActionRequest: action:Action; source_intent:str
class SafeActionGate:
    def authorize(self,action:str,intent:str):
        try:a=Action(action)
        except ValueError:return ActionRequest(Action.NO_ACTION,intent)
        return ActionRequest(a,intent) if a in SAFE else ActionRequest(Action.NO_ACTION,intent)
