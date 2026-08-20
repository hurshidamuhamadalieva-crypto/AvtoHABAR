from aiogram.fsm.state import State, StatesGroup


class PaymentFlow(StatesGroup):
    choosing_plan = State()
    choosing_method = State()
    waiting_screenshot = State()
    waiting_click_confirm = State()


class PhoneFlow(StatesGroup):
    agreeing = State()
    entering_phone = State()
    entering_code = State()
    entering_password = State()


class GroupFlow(StatesGroup):
    choosing_folder = State()


class BroadcastFlow(StatesGroup):
    """
    E'lon matni + interval + yuborishni boshlash — bittalashtirilgan,
    ketma-ket (bot2 uslubidagi) oqim. Avvalgi alohida
    E'lonlar / Interval / Yuborishni boshlash bo'limlari shu bittaga
    birlashtirildi.
    """
    waiting_text = State()
    choosing_interval = State()
    confirming = State()
    active = State()


class AdminFlow(StatesGroup):
    panel = State()
    viewing_users = State()
    user_detail = State()
    broadcast = State()
    ban_input = State()
    edit_sub = State()