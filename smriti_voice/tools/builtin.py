"""Tool handlers and the default registry wiring.

Read-only tools are the bulk of the system.  Three controlled actions exist
(call a trusted contact, create a reminder, start a game); each one stops for a
spoken confirmation and each one ultimately executes through the deterministic
:class:`~smriti_voice.actions.SafeActionGate`, never directly from the model.

Two tools are registered as ``SENSITIVE`` on purpose — ``change_medication`` and
``transfer_money``.  They exist so that a model which has been talked into
"using the medication tool" hits a registered refusal with a clear audit trail,
rather than an ambiguous "unknown tool".  They can never execute: the registry
refuses every ``SENSITIVE`` tool before the handler is reached, and they are not
advertised to the model.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..actions import Action
from ..exceptions import WeatherError
from ..safety.authorization import Principal
from ..safety.policy import SafetyPolicy
from ..schemas import PermissionLevel, SafetyLevel
from .registry import Tool, ToolContext, ToolRegistry
from .schemas import (
    AppointmentQuery,
    CallFamilyArgs,
    CreateReminderArgs,
    DateQuery,
    EmptyArgs,
    FamilyMemberQuery,
    MedicationQuery,
    MemorySearchQuery,
    OpenAppArgs,
    PhoneHelpArgs,
    StartGameArgs,
    StrictModel,
    WeatherQuery,
)

# Screens the assistant is allowed to open, mapped onto v4.1 actions.
APP_SCREENS: dict[str, str] = {
    'play': Action.OPEN_PLAY.value,
    'games': Action.OPEN_PLAY.value,
    'family': Action.OPEN_MY_PEOPLE.value,
    'my_people': Action.OPEN_MY_PEOPLE.value,
    'people': Action.OPEN_MY_PEOPLE.value,
    'today': Action.OPEN_TODAY.value,
    'schedule': Action.OPEN_TODAY.value,
    'medicine': Action.OPEN_MEDICINE.value,
    'help': Action.HELP.value,
}

# Step-by-step phone guidance.  Kept as data, not model invention, so the steps
# are stable and reviewable.  The model rephrases them into the user's language.
PHONE_GUIDES: dict[str, list[str]] = {
    'open whatsapp': [
        'Look for the green circle with a white telephone inside it.',
        'Tap that picture once with your finger.',
        'Wait a moment. WhatsApp will open by itself.',
    ],
    'make a call': [
        'Find the green telephone picture at the bottom of the screen.',
        'Tap it once.',
        'Tap the name of the person you want to call.',
    ],
    'send a message': [
        'Open WhatsApp by tapping the green circle.',
        'Tap the name of the person you want to write to.',
        'Tap the white box at the bottom and speak or type your message.',
        'Tap the small arrow on the right to send it.',
    ],
    'increase the volume': [
        'Look at the left edge of your phone for two small buttons.',
        'Press the upper one a few times.',
        'You will see the volume bar go up on the screen.',
    ],
    'turn on wifi': [
        'Put one finger at the very top of the screen and slide it down.',
        'Look for the picture that looks like a small fan or curved lines.',
        'Tap it once. It will turn bright when Wi-Fi is on.',
    ],
    'charge the phone': [
        'Find the cable that came with your phone.',
        'Put the small end into the hole at the bottom of the phone.',
        'Put the other end into the wall socket and switch it on.',
    ],
}

PHONE_TOPIC_ALIASES: dict[str, str] = {
    'whatsapp': 'open whatsapp', 'open whatsapp': 'open whatsapp',
    'call': 'make a call', 'make a call': 'make a call', 'phone call': 'make a call',
    'message': 'send a message', 'send a message': 'send a message', 'sms': 'send a message',
    'volume': 'increase the volume', 'increase the volume': 'increase the volume',
    'sound': 'increase the volume',
    'wifi': 'turn on wifi', 'wi-fi': 'turn on wifi', 'turn on wifi': 'turn on wifi',
    'internet': 'turn on wifi',
    'charge': 'charge the phone', 'charging': 'charge the phone', 'battery': 'charge the phone',
}


# --------------------------------------------------------------------------- #
# Read-only handlers
# --------------------------------------------------------------------------- #
def _get_family_member(principal: Principal, args: FamilyMemberQuery,
                       context: ToolContext) -> dict[str, Any]:
    members = context.memory.find_family(principal.user_id, name=args.name,
                                         relation=args.relation)
    return {'members': members, 'count': len(members),
            'found': bool(members),
            'note': ('No family member matches that. Do not guess a name.'
                     if not members else None)}


def _get_family_members(principal: Principal, args: EmptyArgs,
                        context: ToolContext) -> dict[str, Any]:
    members = context.memory.list_family(principal.user_id)
    return {'members': members, 'count': len(members)}


def _get_personal_memory(principal: Principal, args: MemorySearchQuery,
                         context: ToolContext) -> dict[str, Any]:
    return context.memory.search_memories(principal.user_id, args.query, limit=args.limit)


def _get_meal_history(principal: Principal, args: DateQuery,
                      context: ToolContext) -> dict[str, Any]:
    return context.memory.meals(principal.user_id, args.when)


def _get_today_schedule(principal: Principal, args: DateQuery,
                        context: ToolContext) -> dict[str, Any]:
    return context.memory.schedule(principal.user_id, args.when)


def _get_medication_schedule(principal: Principal, args: MedicationQuery,
                             context: ToolContext) -> dict[str, Any]:
    return context.memory.medicines(principal.user_id, args.time_of_day)


def _get_appointment(principal: Principal, args: AppointmentQuery,
                     context: ToolContext) -> dict[str, Any]:
    return context.memory.appointments(principal.user_id, args.when, days=args.days)


def _get_visitors(principal: Principal, args: DateQuery,
                  context: ToolContext) -> dict[str, Any]:
    return context.memory.visitors(principal.user_id, args.when)


def _get_reminders(principal: Principal, args: EmptyArgs,
                   context: ToolContext) -> dict[str, Any]:
    return context.memory.reminders(principal.user_id)


def _get_games(principal: Principal, args: EmptyArgs,
               context: ToolContext) -> dict[str, Any]:
    return context.memory.games(principal.user_id)


def _get_current_time(principal: Principal, args: EmptyArgs,
                      context: ToolContext) -> dict[str, Any]:
    now = datetime.now(ZoneInfo('Asia/Kolkata'))
    return {'iso': now.isoformat(timespec='minutes'),
            'time': now.strftime('%I:%M %p').lstrip('0'),
            'date': now.date().isoformat(),
            'weekday': now.strftime('%A'),
            'timezone': 'Asia/Kolkata'}


def _get_weather(principal: Principal, args: WeatherQuery,
                 context: ToolContext) -> dict[str, Any]:
    provider = context.weather
    if provider is None:
        return {'available': False, 'reason': 'NO_WEATHER_PROVIDER',
                'note': 'Weather is unavailable. Do not invent a forecast.'}
    extras = context.extras or {}
    try:
        reading = provider.get_weather(
            latitude=extras.get('latitude', 26.1445),
            longitude=extras.get('longitude', 91.7362),
            location=args.location or extras.get('location', 'your area'))
    except WeatherError as exc:
        return {'available': False, 'reason': 'WEATHER_UNAVAILABLE', 'detail': str(exc),
                'note': 'Weather is unavailable. Tell the user honestly; do not invent it.'}
    return {'available': True, **reading.to_dict()}


def _get_phone_help(principal: Principal, args: PhoneHelpArgs,
                    context: ToolContext) -> dict[str, Any]:
    topic = (args.topic or '').strip().lower()
    key = PHONE_TOPIC_ALIASES.get(topic)
    if key is None:
        for alias, canonical in PHONE_TOPIC_ALIASES.items():
            if alias in topic:
                key = canonical
                break
    if key is None or key not in PHONE_GUIDES:
        return {'found': False, 'topic': topic,
                'available_topics': sorted(set(PHONE_GUIDES)),
                'note': 'No stored guide for this. Give short, simple, general steps instead.'}
    return {'found': True, 'topic': key, 'steps': PHONE_GUIDES[key],
            'style': 'Give ONE step at a time and wait for the user.'}


def _open_app(principal: Principal, args: OpenAppArgs, context: ToolContext) -> dict[str, Any]:
    action = APP_SCREENS.get((args.app or '').strip().lower())
    if not action:
        return {'opened': False, 'reason': 'UNKNOWN_SCREEN',
                'available': sorted(set(APP_SCREENS))}
    return {'opened': True, 'screen': args.app, 'action': action}


# --------------------------------------------------------------------------- #
# Controlled actions (confirmation required; executed by the deterministic gate)
# --------------------------------------------------------------------------- #
def _call_family_member(principal: Principal, args: CallFamilyArgs,
                        context: ToolContext) -> dict[str, Any]:
    """Only reached after confirmation.  Trust is re-checked here, not assumed."""
    member = context.memory.trusted_contact(principal.user_id, args.name)
    if member is None:
        return {'called': False, 'reason': 'NOT_A_TRUSTED_CONTACT',
                'note': 'Only saved, trusted family contacts can be called.'}
    return {'called': True, 'name': member.name, 'relation': member.relation,
            'action': Action.CALL_PRIMARY_CONTACT.value, 'contact_id': member.id}


def _create_reminder(principal: Principal, args: CreateReminderArgs,
                     context: ToolContext) -> dict[str, Any]:
    from ..memory.models import Reminder
    from ..memory.provenance import provenance_for_write
    reminder = context.memory.repo.add_reminder(Reminder(
        user_id=principal.user_id, text=args.text, remind_at=args.remind_at,
        provenance=provenance_for_write(source='user', created_by=principal.user_id)))
    return {'created': True, 'text': reminder.text, 'remind_at': reminder.remind_at,
            'verification_status': reminder.provenance.verification_status,
            'action': 'CREATE_REMINDER'}


def _start_game(principal: Principal, args: StartGameArgs,
                context: ToolContext) -> dict[str, Any]:
    games = context.memory.games(principal.user_id)['games']
    if args.game:
        match = next((g for g in games if g['key'] == args.game or
                      g['name'].lower() == args.game.lower()), None)
        if match is None:
            return {'started': False, 'reason': 'UNKNOWN_GAME', 'available': games}
        return {'started': True, 'game': match, 'action': Action.OPEN_PLAY.value}
    return {'started': True, 'game': None, 'action': Action.OPEN_PLAY.value,
            'available': games}


# --------------------------------------------------------------------------- #
# Sensitive tools: registered so refusals are explicit and auditable.
# --------------------------------------------------------------------------- #
def _never_executes(principal: Principal, args: StrictModel,
                    context: ToolContext) -> dict[str, Any]:
    raise AssertionError('A SENSITIVE tool handler must never be reached')


def build_default_registry(policy: SafetyPolicy) -> ToolRegistry:
    """The production tool set."""
    registry = ToolRegistry(policy)

    read_only = [
        ('get_family_member', 'Look up one family member by name or relationship '
         '(for example the user\'s daughter). Use this before saying any family name.',
         FamilyMemberQuery, _get_family_member),
        ('get_family_members', 'List every family member saved for the user.',
         EmptyArgs, _get_family_members),
        ('get_personal_memory', 'Search the user\'s saved personal memories and stories.',
         MemorySearchQuery, _get_personal_memory),
        ('get_meal_history', 'What the user ate on a given day.', DateQuery, _get_meal_history),
        ('get_today_schedule', 'The user\'s routine, appointments and visitors for a day.',
         DateQuery, _get_today_schedule),
        ('get_medication_schedule', 'Read the user\'s medicine schedule. READ ONLY: this '
         'tool can never change a medicine or a dose.', MedicationQuery, _get_medication_schedule),
        ('get_appointment', 'The user\'s appointments on or after a date.',
         AppointmentQuery, _get_appointment),
        ('get_visitors', 'Who is visiting the user on a given day.', DateQuery, _get_visitors),
        ('get_reminders', 'The user\'s active reminders.', EmptyArgs, _get_reminders),
        ('get_games', 'The cognitive games available to this user.', EmptyArgs, _get_games),
        ('get_current_time', 'The current date and time. Use this instead of guessing.',
         EmptyArgs, _get_current_time),
        ('get_weather', 'The current weather. Use this instead of guessing; if it reports '
         'unavailable, say so honestly.', WeatherQuery, _get_weather),
        ('get_phone_help', 'Stored step-by-step instructions for using the phone.',
         PhoneHelpArgs, _get_phone_help),
    ]
    for name, description, model, handler in read_only:
        registry.register(Tool(name=name, description=description, args_model=model,
                               handler=handler, safety_level=SafetyLevel.READ_ONLY))

    registry.register(Tool(
        name='open_app', description='Open one of the SMRITI screens: play, family, today, '
                                     'medicine or help.',
        args_model=OpenAppArgs, handler=_open_app,
        safety_level=SafetyLevel.CONTROLLED_ACTION, action=None))

    registry.register(Tool(
        name='call_family_member',
        description='Ask to call a saved, trusted family member. The user is always asked to '
                    'confirm first, and unknown numbers can never be dialled.',
        args_model=CallFamilyArgs, handler=_call_family_member,
        safety_level=SafetyLevel.CONTROLLED_ACTION, action=Action.CALL_PRIMARY_CONTACT.value,
        requires_confirmation=True))

    registry.register(Tool(
        name='create_reminder', description='Save a simple reminder for the user, after they '
                                            'confirm it. Never use this for medication.',
        args_model=CreateReminderArgs, handler=_create_reminder,
        safety_level=SafetyLevel.CONTROLLED_ACTION, action='CREATE_REMINDER',
        requires_confirmation=True))

    registry.register(Tool(
        name='start_game', description='Start a cognitive game, after the user confirms.',
        args_model=StartGameArgs, handler=_start_game,
        safety_level=SafetyLevel.CONTROLLED_ACTION, action='START_GAME',
        requires_confirmation=True))

    # Never advertised, never executable. Present so an attempt is logged as a
    # refusal of a known-sensitive capability rather than an unknown name.
    for name, description in (
        ('change_medication', 'BLOCKED: medication changes require a caregiver or doctor.'),
        ('delete_record', 'BLOCKED: deleting saved records requires a caregiver.'),
        ('transfer_money', 'BLOCKED: SMRITI never moves money.'),
        ('call_number', 'BLOCKED: SMRITI never dials an arbitrary phone number.'),
    ):
        registry.register(Tool(name=name, description=description, args_model=StrictModel,
                               handler=_never_executes, safety_level=SafetyLevel.SENSITIVE,
                               permission=PermissionLevel.CAREGIVER))

    return registry
