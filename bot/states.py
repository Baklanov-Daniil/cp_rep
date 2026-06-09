from aiogram.fsm.state import State, StatesGroup


class AuthStates(StatesGroup):
    waiting_for_company = State()
    waiting_for_project = State()
