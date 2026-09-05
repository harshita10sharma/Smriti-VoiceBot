"""Deterministic answers that work with no model at all.

This is what keeps SMRITI useful when the device is offline and no local LLM is
installed.  It is keyword routing over the same tools the model would have
called, with fixed sentence templates.  It never invents a fact: every value in
a reply comes from the database.

Templates exist for the four languages whose wording has been reviewed here
(English, Hindi, Assamese, Bengali).  For any other language the fallback
reports honestly that it cannot answer without the assistant, rather than
replying in the wrong language.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..normalize import normalize_text
from ..safety.authorization import Principal
from ..schemas import ToolCall
from ..tools.registry import ToolContext, ToolRegistry

TEMPLATE_LANGUAGES = frozenset({'eng', 'hin', 'asm', 'ben'})

# (tool name, arguments, keyword triggers)
ROUTES: list[tuple[str, dict, tuple[str, ...]]] = [
    ('get_family_members', {}, ('my family', 'family members', 'who is my family',
                                'tell me about my family', 'show my family', 'my people')),
    ('get_meal_history', {'when': 'yesterday'}, ('eat yesterday', 'ate yesterday',
                                                 'what did i eat yesterday')),
    ('get_meal_history', {'when': 'today'}, ('eat today', 'ate today', 'what did i eat')),
    ('get_medication_schedule', {}, ('my medicine', 'medicines', 'medication', 'what medicine',
                                     'when is my medicine', 'medicine time')),
    ('get_today_schedule', {'when': 'today'}, ('my day', 'today', 'schedule', 'routine',
                                               'what should i do today')),
    ('get_appointment', {'when': 'today'}, ('appointment', 'doctor visit')),
    ('get_visitors', {'when': 'today'}, ('visiting me', 'who is visiting', 'visitor')),
    ('get_reminders', {}, ('my reminders', 'reminders', 'remind me what')),
    ('get_games', {}, ('my games', 'what game', 'which game', 'games')),
    ('get_current_time', {}, ('what time', 'the time', 'what is today', "what's today",
                              'what day')),
]

RELATION_WORDS = {
    'daughter': 'daughter', 'son': 'son', 'wife': 'wife', 'husband': 'husband',
    'granddaughter': 'granddaughter', 'grandson': 'grandson', 'sister': 'sister',
    'brother': 'brother',
}

UNAVAILABLE: dict[str, str] = {
    'eng': "I cannot look that up right now. I can still tell you about your family, your day, "
           "your medicine times or your games.",
    'hin': "मैं अभी यह नहीं देख सकती। मैं आपके परिवार, आपके दिन, दवा के समय या खेलों के बारे में बता सकती हूँ।",
    'asm': "মই এতিয়া সেইটো চাব নোৱাৰো। কিন্তু আপোনাৰ পৰিয়াল, দিনৰ কাম, ঔষধৰ সময় বা খেলৰ বিষয়ে ক'ব পাৰো।",
    'ben': "আমি এখন সেটি দেখতে পারছি না। তবে আপনার পরিবার, দিনের কাজ, ওষুধের সময় বা খেলার কথা বলতে পারি।",
}

NOTHING_SAVED: dict[str, str] = {
    'eng': "I do not have that written down. Shall I ask your caregiver to add it?",
    'hin': "मेरे पास यह लिखा हुआ नहीं है। क्या मैं आपके देखभाल करने वाले से इसे जोड़ने को कहूँ?",
    'asm': "মোৰ ওচৰত এইটো লিখা নাই। আপোনাৰ যত্ন লোৱা জনক যোগ কৰিবলৈ ক'ম নেকি?",
    'ben': "আমার কাছে এটি লেখা নেই। আপনার পরিচর্যাকারীকে যোগ করতে বলব কি?",
}

NO_TEMPLATE: dict[str, str] = {
    'eng': "I can only answer simple saved questions right now, because I am not connected.",
}


@dataclass
class FallbackAnswer:
    text: str
    tool_name: str | None
    data: dict | None
    answered: bool


class DeterministicResponder:
    """Answers a narrow set of personal questions without any model."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    # ------------------------------------------------------------------ #
    def answer(self, utterance: str, *, principal: Principal, context: ToolContext,
               language: str) -> FallbackAnswer:
        if language not in TEMPLATE_LANGUAGES:
            return FallbackAnswer(NO_TEMPLATE['eng'], None, None, False)

        query = normalize_text(utterance)

        # "What is my daughter's name?" style questions come first: they are the
        # single most common request and they have an exact answer.
        for word, relation in RELATION_WORDS.items():
            if word in query:
                result = self._run('get_family_member', {'relation': relation},
                                   principal, context)
                members = (result or {}).get('members') or []
                if members:
                    return FallbackAnswer(self._family_sentence(members, language),
                                          'get_family_member', result, True)
                return FallbackAnswer(NOTHING_SAVED[language], 'get_family_member', result, True)

        for tool_name, arguments, triggers in ROUTES:
            if any(trigger in query for trigger in triggers):
                result = self._run(tool_name, arguments, principal, context)
                if result is None:
                    break
                return FallbackAnswer(self._render(tool_name, result, language),
                                      tool_name, result, True)

        return FallbackAnswer(UNAVAILABLE[language], None, None, False)

    # ------------------------------------------------------------------ #
    def _run(self, name: str, arguments: dict, principal: Principal,
             context: ToolContext) -> dict | None:
        result = self.registry.execute(ToolCall(name=name, arguments=arguments),
                                       principal, context)
        return result.data if result.ok and result.executed else None

    @staticmethod
    def _family_sentence(members: list[dict], language: str) -> str:
        member = members[0]
        name, relation = member['name'], member['relation']
        lives = member.get('lives_in')
        if language == 'hin':
            text = f"आपकी {relation} का नाम {name} है।"
            return text + (f" वे {lives} में रहती हैं।" if lives else '')
        if language == 'asm':
            text = f"আপোনাৰ {relation}ৰ নাম {name}।"
            return text + (f" তেওঁ {lives}ত থাকে।" if lives else '')
        if language == 'ben':
            text = f"আপনার {relation}-এর নাম {name}।"
            return text + (f" তিনি {lives}-এ থাকেন।" if lives else '')
        text = f"Your {relation} is called {name}."
        return text + (f" She lives in {lives}." if lives else '')

    def _render(self, tool_name: str, data: dict, language: str) -> str:
        renderer = getattr(self, f'_render_{tool_name}', None)
        if renderer is None:
            return UNAVAILABLE[language]
        return renderer(data, language)

    # -- per-tool renderers -------------------------------------------- #
    def _render_get_family_members(self, data: dict, language: str) -> str:
        members = data.get('members') or []
        if not members:
            return NOTHING_SAVED[language]
        listed = ', '.join(f"{m['name']} ({m['relation']})" for m in members[:4])
        return {
            'eng': f'Your family: {listed}.',
            'hin': f'आपका परिवार: {listed}।',
            'asm': f'আপোনাৰ পৰিয়াল: {listed}।',
            'ben': f'আপনার পরিবার: {listed}।',
        }[language]

    def _render_get_meal_history(self, data: dict, language: str) -> str:
        meals = data.get('meals') or []
        if not meals:
            return NOTHING_SAVED[language]
        listed = ', '.join(f"{m['meal_type']}: {m['description']}" for m in meals)
        return {
            'eng': f'You had {listed}.',
            'hin': f'आपने खाया — {listed}।',
            'asm': f'আপুনি খাইছিল — {listed}।',
            'ben': f'আপনি খেয়েছিলেন — {listed}।',
        }[language]

    def _render_get_medication_schedule(self, data: dict, language: str) -> str:
        medicines = data.get('medicines') or []
        if not medicines:
            return NOTHING_SAVED[language]
        listed = ', '.join(f"{m['name']} {m['dosage'] or ''} at {m['schedule_time'] or ''}".strip()
                           for m in medicines)
        return {
            'eng': f'Your medicines: {listed}. Only your doctor can change these.',
            'hin': f'आपकी दवाएँ: {listed}। इन्हें केवल आपके डॉक्टर बदल सकते हैं।',
            'asm': f'আপোনাৰ ঔষধ: {listed}। কেৱল ডাক্তৰেহে সলনি কৰিব পাৰে।',
            'ben': f'আপনার ওষুধ: {listed}। শুধু আপনার ডাক্তার পরিবর্তন করতে পারেন।',
        }[language]

    def _render_get_today_schedule(self, data: dict, language: str) -> str:
        parts: list[str] = []
        for item in (data.get('routine') or [])[:3]:
            parts.append(f"{item['title']} {item.get('time') or ''}".strip())
        for item in (data.get('appointments') or [])[:2]:
            parts.append(f"{item['title']} {item.get('time') or ''}".strip())
        if not parts:
            return NOTHING_SAVED[language]
        listed = ', '.join(parts)
        return {
            'eng': f'Today: {listed}.',
            'hin': f'आज: {listed}।',
            'asm': f'আজি: {listed}।',
            'ben': f'আজ: {listed}।',
        }[language]

    def _render_get_appointment(self, data: dict, language: str) -> str:
        items = data.get('appointments') or []
        if not items:
            return {'eng': 'You have no appointment written down for today.',
                    'hin': 'आज आपकी कोई अपॉइंटमेंट नहीं लिखी है।',
                    'asm': 'আজিৰ বাবে কোনো এপইণ্টমেণ্ট লিখা নাই।',
                    'ben': 'আজকের জন্য কোনো অ্যাপয়েন্টমেন্ট লেখা নেই।'}[language]
        first = items[0]
        listed = f"{first['title']} {first.get('time') or ''}".strip()
        return {
            'eng': f'You have {listed}.',
            'hin': f'आपकी अपॉइंटमेंट है — {listed}।',
            'asm': f'আপোনাৰ এপইণ্টমেণ্ট আছে — {listed}।',
            'ben': f'আপনার অ্যাপয়েন্টমেন্ট আছে — {listed}।',
        }[language]

    def _render_get_visitors(self, data: dict, language: str) -> str:
        visitors = data.get('visitors') or []
        if not visitors:
            return {'eng': 'Nobody is written down as visiting today.',
                    'hin': 'आज किसी के आने की बात नहीं लिखी है।',
                    'asm': 'আজি কোনো আহিব বুলি লিখা নাই।',
                    'ben': 'আজ কারও আসার কথা লেখা নেই।'}[language]
        listed = ', '.join(v['name'] for v in visitors)
        return {
            'eng': f'{listed} is coming to see you today.',
            'hin': f'आज {listed} आपसे मिलने आ रहे हैं।',
            'asm': f'আজি {listed} আপোনাক লগ কৰিবলৈ আহিছে।',
            'ben': f'আজ {listed} আপনার সঙ্গে দেখা করতে আসছেন।',
        }[language]

    def _render_get_reminders(self, data: dict, language: str) -> str:
        reminders = data.get('reminders') or []
        if not reminders:
            return NOTHING_SAVED[language]
        listed = ', '.join(r['text'] for r in reminders[:3])
        return {'eng': f'Your reminders: {listed}.',
                'hin': f'आपके रिमाइंडर: {listed}।',
                'asm': f'আপোনাৰ মনত পেলোৱা: {listed}।',
                'ben': f'আপনার রিমাইন্ডার: {listed}।'}[language]

    def _render_get_games(self, data: dict, language: str) -> str:
        games = data.get('games') or []
        if not games:
            return NOTHING_SAVED[language]
        listed = ', '.join(g['name'] for g in games)
        return {'eng': f'You can play: {listed}.',
                'hin': f'आप खेल सकते हैं: {listed}।',
                'asm': f'আপুনি খেলিব পাৰে: {listed}।',
                'ben': f'আপনি খেলতে পারেন: {listed}।'}[language]

    def _render_get_current_time(self, data: dict, language: str) -> str:
        time_text, weekday = data.get('time', ''), data.get('weekday', '')
        return {'eng': f'It is {time_text}, {weekday}.',
                'hin': f'अभी {time_text} बजे हैं, {weekday}।',
                'asm': f'এতিয়া {time_text} বাজিছে, {weekday}।',
                'ben': f'এখন {time_text} বাজে, {weekday}।'}[language]
