"""The turn router.

Order matters, and it is the whole safety argument of the system:

1. **Prompt-injection screen** on what the user said.
2. **Deterministic safety screen** — a sensitive request is refused here, before
   any model sees it.
3. **Pending confirmation** — a stored controlled action resolves on an explicit
   yes, never on silence or on an ambiguous reply.
4. **v4.1 command router** — unchanged. If it accepts, the turn is a command and
   no model is involved at all.
5. **Conversation** — only now does an LLM run, and only with validated tools.
6. **Deterministic fallback** — if no model can answer, saved questions are still
   answered from the database.

The model can propose. Only steps 2, 4 and the tool registry can authorise.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..actions import Action, SafeActionGate
from ..config import AppConfig
from ..exceptions import LLMError
from ..intents import IntentRouter
from ..language.registry import LanguageService
from ..llm.base import Message, ToolSpec
from ..llm.router import LLMRouter
from ..logging import get_logger
from ..normalize import normalize_text
from ..safety.authorization import Principal
from ..safety.confirmation import ConfirmationManager, cancellation_text
from ..safety.policy import SafetyPolicy, refusal_text
from ..safety.prompt_injection import screen_user_input
from ..schemas import (
    ConversationResponse,
    ExecutionMode,
    SafetyDecision,
    ToolCall,
    ToolResult,
    TurnKind,
    TurnMetadata,
)
from ..tools.registry import ToolContext, ToolRegistry
from .context import ConversationSession, SessionStore
from .policy import DeterministicResponder
from .prompts import build_system_prompt, untrusted_block

log = get_logger('conversation')

MAX_TOOL_ROUNDS = 2

# The v4.1 router was built for a closed command vocabulary, where an utterance is
# a short phrase. A conversational question is longer, and fuzzy matching it
# against command phrases misfires: "what is the weather today" scores highly
# against the "what is today" phrase and would open the schedule screen instead of
# answering. So the command branch only runs on utterances that are actually
# command-shaped. Every command in the v4.1 regression suite is three tokens or
# fewer, so this changes none of them; longer requests reach the model, which can
# still open a screen through the `open_app` tool.
MAX_COMMAND_TOKENS = 4

# Actions that place a phone call are never executed straight from one utterance
# in the conversational path: they become a pending confirmation. The legacy
# /v1/command endpoint keeps its original v4.1 behaviour, unchanged.
CALL_ACTIONS = {Action.CALL_BINA.value, Action.CALL_PRIMARY_CONTACT.value}

# Spoken confirmation for each deterministic action, in the languages whose
# wording has been reviewed. Other languages fall back to English text with the
# action still returned, so the UI can act even when we cannot speak.
ACTION_REPLIES: dict[str, dict[str, str]] = {
    Action.OPEN_PLAY.value: {
        'eng': 'Opening your games now.', 'hin': 'आपके खेल खोल रही हूँ।',
        'asm': 'আপোনাৰ খেল খুলি আছোঁ।', 'ben': 'আপনার খেলা খুলছি।'},
    Action.OPEN_MY_PEOPLE.value: {
        'eng': 'Here are your people.', 'hin': 'यह रहे आपके अपने लोग।',
        'asm': 'এইয়া আপোনাৰ মানুহবোৰ।', 'ben': 'এই যে আপনার মানুষজন।'},
    Action.OPEN_TODAY.value: {
        'eng': 'Here is your day.', 'hin': 'यह रहा आपका दिन।',
        'asm': 'এইয়া আপোনাৰ দিনটো।', 'ben': 'এই যে আপনার দিনটি।'},
    Action.OPEN_MEDICINE.value: {
        'eng': 'Here is your medicine list.', 'hin': 'यह रही आपकी दवाओं की सूची।',
        'asm': 'এইয়া আপোনাৰ ঔষধৰ তালিকা।', 'ben': 'এই যে আপনার ওষুধের তালিকা।'},
    Action.HELP.value: {
        'eng': 'I can show your family, your day, your medicines, or a game. What would you like?',
        'hin': 'मैं आपका परिवार, आपका दिन, दवाएँ या कोई खेल दिखा सकती हूँ। आप क्या चाहेंगे?',
        'asm': 'মই আপোনাৰ পৰিয়াল, দিন, ঔষধ বা খেল দেখুৱাব পাৰোঁ। কি লাগে?',
        'ben': 'আমি আপনার পরিবার, দিন, ওষুধ বা খেলা দেখাতে পারি। কী চান?'},
    Action.STOP.value: {
        'eng': 'Alright, I have stopped.', 'hin': 'ठीक है, मैंने रोक दिया।',
        'asm': 'ঠিক আছে, বন্ধ কৰিলোঁ।', 'ben': 'ঠিক আছে, থামিয়ে দিলাম।'},
    Action.CALL_BINA.value: {
        'eng': 'Shall I call them now? Please say yes or no.',
        'hin': 'क्या मैं अभी फ़ोन करूँ? हाँ या नहीं कहिए।',
        'asm': 'এতিয়া ফোন কৰিম নেকি? হয় বা নহয় কওক।',
        'ben': 'এখন ফোন করব কি? হ্যাঁ বা না বলুন।'},
    Action.CALL_PRIMARY_CONTACT.value: {
        'eng': 'Shall I call them now? Please say yes or no.',
        'hin': 'क्या मैं अभी फ़ोन करूँ? हाँ या नहीं कहिए।',
        'asm': 'এতিয়া ফোন কৰিম নেকি? হয় বা নহয় কওক।',
        'ben': 'এখন ফোন করব কি? হ্যাঁ বা না বলুন।'},
}

UNCLEAR: dict[str, str] = {
    'eng': "I did not quite catch that. Could you say it once more, please?",
    'hin': "मैं ठीक से समझ नहीं पाई। कृपया एक बार फिर कहिए।",
    'asm': "মই ভালদৰে বুজি নাপালোঁ। অনুগ্ৰহ কৰি আকৌ এবাৰ কওক।",
    'ben': "আমি ঠিক বুঝতে পারিনি। অনুগ্রহ করে আরেকবার বলুন।",
}

CONFIRM_AGAIN: dict[str, str] = {
    'eng': 'Sorry, please just say yes or no.',
    'hin': 'क्षमा कीजिए, कृपया केवल हाँ या नहीं कहिए।',
    'asm': 'ক্ষমা কৰিব, কেৱল হয় বা নহয় কওক।',
    'ben': 'দুঃখিত, শুধু হ্যাঁ বা না বলুন।',
}

PRONOUNS = ('she', 'he', 'her', 'him', 'his', 'they', 'them')


def _text_for(table: dict[str, dict[str, str]] | dict[str, str], key: str,
              language: str) -> str:
    entry = table.get(key, {}) if isinstance(next(iter(table.values()), None), dict) else table
    if isinstance(entry, dict):
        return entry.get(language) or entry.get('eng', '')
    return str(entry)


def resolve_pronouns(text: str, subject: str | None) -> str:
    """Rewrite 'where does she live' into 'where does Bina live' for the
    deterministic path.  The LLM path gets the real history instead."""
    if not subject:
        return text
    words = text.split()
    rewritten = [subject if normalize_text(word) in PRONOUNS else word for word in words]
    return ' '.join(rewritten)


@dataclass
class TurnOutcome:
    """Internal result before it is shaped into the API response."""
    text: str
    kind: TurnKind
    action: str = Action.NO_ACTION.value
    action_accepted: bool = False
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    safety: SafetyDecision | None = None
    requires_confirmation: bool = False
    llm_provider: str | None = None
    llm_latency_ms: int = 0
    tool_latency_ms: int = 0
    fallback_used: bool = False
    error_code: str | None = None


class ConversationManager:
    def __init__(self, config: AppConfig, *, memory, registry: ToolRegistry,
                 llm: LLMRouter, languages: LanguageService, policy: SafetyPolicy,
                 sessions: SessionStore | None = None, weather: Any = None,
                 offline_manager: Any = None, telemetry: Any = None) -> None:
        self.config = config
        self.memory = memory
        self.registry = registry
        self.llm = llm
        self.languages = languages
        self.policy = policy
        self.confirmations = ConfirmationManager(policy)
        self.responder = DeterministicResponder(registry)
        self.gate = SafeActionGate()
        self.sessions = sessions or SessionStore(
            max_turns=config.max_history_turns,
            idle_timeout_minutes=config.max_session_idle_minutes)
        self.weather = weather
        self.offline_manager = offline_manager
        self.telemetry = telemetry
        self._command_routers: dict[str, IntentRouter] = {}

    # ------------------------------------------------------------------ #
    def command_router(self, language: str) -> IntentRouter | None:
        """The v4.1 router for one language.  Never borrows another language."""
        if language in self._command_routers:
            return self._command_routers[language]
        try:
            pack = self.languages.pack(language)
        except KeyError:
            return None
        router = IntentRouter(pack.commands_path, self.config.settings.intent_threshold,
                              self.config.settings.ambiguity_margin)
        self._command_routers[language] = router
        return router

    # ------------------------------------------------------------------ #
    def handle(self, *, user_id: str, message: str, session_id: str | None = None,
               language: str | None = None, request_id: str | None = None,
               offline: bool | None = None) -> ConversationResponse:
        started = time.perf_counter()
        rid = request_id or uuid.uuid4().hex
        session = self.sessions.get_or_create(session_id, user_id,
                                              language or 'eng')
        session.expire_pending()
        turn_language = language or session.language or 'eng'
        session.language = turn_language
        principal = Principal(user_id)
        is_offline = bool(offline) if offline is not None else (
            not self.offline_manager.is_online() if self.offline_manager else False)

        session.add_turn('user', message, language=turn_language)
        outcome = self._route(message, session=session, principal=principal,
                              language=turn_language, request_id=rid, offline=is_offline)
        session.add_turn('assistant', outcome.text, language=turn_language, kind=outcome.kind)

        mode = (ExecutionMode.OFFLINE_PRIMARY if is_offline and outcome.llm_provider == 'local'
                else ExecutionMode.DEGRADED if outcome.kind is TurnKind.FALLBACK
                else ExecutionMode.ERROR if outcome.kind is TurnKind.ERROR
                else ExecutionMode.ONLINE_PRIMARY if outcome.llm_provider
                else ExecutionMode.DEGRADED)

        metadata = TurnMetadata(
            request_id=rid, session_id=session.session_id, kind=outcome.kind,
            execution_mode=mode, offline=is_offline, fallback_used=outcome.fallback_used,
            llm_provider=outcome.llm_provider, llm_latency_ms=outcome.llm_latency_ms,
            tool_latency_ms=outcome.tool_latency_ms,
            total_latency_ms=int((time.perf_counter() - started) * 1000),
            error_code=outcome.error_code)

        self._persist(session, message, outcome, metadata)

        return ConversationResponse(
            request_id=rid, session_id=session.session_id, response_text=outcome.text,
            language=turn_language, kind=outcome.kind, action=outcome.action,
            action_accepted=outcome.action_accepted, tool_calls=outcome.tool_calls,
            tool_results=outcome.tool_results, safety=outcome.safety,
            requires_confirmation=outcome.requires_confirmation, metadata=metadata)

    # ------------------------------------------------------------------ #
    def _route(self, message: str, *, session: ConversationSession, principal: Principal,
               language: str, request_id: str, offline: bool) -> TurnOutcome:
        # 1. Prompt injection. An elderly user does not talk like this.
        suspicious, patterns = screen_user_input(message)
        if suspicious:
            log.warning('prompt_injection_blocked',
                        fields={'request_id': request_id, 'patterns': patterns})
            return TurnOutcome(
                text=refusal_text('safety_override', language), kind=TurnKind.REFUSAL,
                safety=SafetyDecision(allowed=False, reason='prompt_injection',
                                      category='prompt_injection',
                                      refusal_key='safety_override', matched=patterns))

        # 2. Deterministic safety screen, before any model.
        verdict = self.policy.screen_utterance(message)
        if not verdict.allowed:
            log.info('utterance_refused', fields={'request_id': request_id,
                                                  'category': verdict.category})
            return TurnOutcome(text=refusal_text(verdict.refusal_key, language),
                               kind=TurnKind.REFUSAL, safety=verdict)

        # 3. A pending confirmation takes priority over everything else.
        if session.pending is not None:
            return self._resolve_confirmation(message, session=session, principal=principal,
                                              language=language, request_id=request_id)

        # 4. The v4.1 deterministic command router. Unchanged behaviour.
        command = self._try_command(message, language=language, session=session,
                                    principal=principal)
        if command is not None:
            return command

        # 5. Conversation with an LLM and validated tools.
        try:
            return self._converse(message, session=session, principal=principal,
                                  language=language, request_id=request_id, offline=offline)
        except LLMError as exc:
            log.info('llm_unavailable', fields={'request_id': request_id,
                                                'error': type(exc).__name__})

        # 6. No model: answer saved questions deterministically.
        return self._fallback(message, session=session, principal=principal,
                              language=language, request_id=request_id)

    # ------------------------------------------------------------------ #
    def _try_command(self, message: str, *, language: str, session: ConversationSession,
                     principal: Principal) -> TurnOutcome | None:
        if len(normalize_text(message).split()) > MAX_COMMAND_TOKENS:
            return None
        router = self.command_router(language)
        if router is None:
            return None
        match = router.classify(message)
        if not match.accepted:
            return None
        granted = self.gate.authorize(match.action, match.intent)
        if granted.action is Action.NO_ACTION:
            return None

        if granted.action.value in CALL_ACTIONS:
            contact = self.memory.trusted_contact(principal.user_id)
            if contact is None:
                return TurnOutcome(text=refusal_text('unknown_number', language),
                                   kind=TurnKind.REFUSAL)
            pending = self.confirmations.create(
                action=Action.CALL_PRIMARY_CONTACT.value, language=language,
                tool_name='call_family_member', arguments={'name': contact.name},
                name=contact.name)
            session.set_pending(pending)
            return TurnOutcome(text=pending.prompt, kind=TurnKind.CONFIRMATION,
                               requires_confirmation=True)

        reply = _text_for(ACTION_REPLIES, granted.action.value, language) or \
            ACTION_REPLIES[Action.HELP.value]['eng']
        return TurnOutcome(text=reply, kind=TurnKind.COMMAND, action=granted.action.value,
                           action_accepted=True)

    # ------------------------------------------------------------------ #
    def _resolve_confirmation(self, message: str, *, session: ConversationSession,
                              principal: Principal, language: str,
                              request_id: str) -> TurnOutcome:
        pending = session.pending
        assert pending is not None
        decision = self.confirmations.resolve(pending, message, language)

        if decision == 'cancelled':
            session.set_pending(None)
            return TurnOutcome(text=cancellation_text(language), kind=TurnKind.CONFIRMATION)

        if decision == 'unclear':
            # Never treat an ambiguous reply as a yes. Ask once more.
            return TurnOutcome(text=_text_for(CONFIRM_AGAIN, language, language)
                               or CONFIRM_AGAIN['eng'],
                               kind=TurnKind.CONFIRMATION, requires_confirmation=True)

        session.set_pending(None)
        if not pending.tool_name:
            return TurnOutcome(text=cancellation_text(language), kind=TurnKind.CONFIRMATION)

        context = self._context(language=language, request_id=request_id,
                                session_id=session.session_id, principal=principal)
        result = self.registry.execute(ToolCall(name=pending.tool_name,
                                                arguments=pending.arguments),
                                       principal, context, confirmed=True)
        action = Action.NO_ACTION.value
        accepted = False
        if result.ok and result.executed:
            proposed = (result.data or {}).get('action')
            if proposed:
                granted = self.gate.authorize(proposed, pending.action)
                action, accepted = granted.action.value, granted.action is not Action.NO_ACTION

        text = self._confirmation_done_text(result, language)
        return TurnOutcome(text=text, kind=TurnKind.CONFIRMATION, action=action,
                           action_accepted=accepted, tool_results=[result],
                           tool_latency_ms=result.latency_ms)

    @staticmethod
    def _confirmation_done_text(result: ToolResult, language: str) -> str:
        data = result.data or {}
        if not result.ok or not result.executed:
            return refusal_text('generic', language)
        if data.get('called'):
            name = data.get('name', '')
            return {'eng': f'Calling {name} now.', 'hin': f'{name} को अभी फ़ोन कर रही हूँ।',
                    'asm': f'{name}ক এতিয়া ফোন কৰিছোঁ।',
                    'ben': f'{name}-কে এখন ফোন করছি।'}.get(language, f'Calling {name} now.')
        if data.get('created'):
            return {'eng': 'I have saved that reminder for you.',
                    'hin': 'मैंने आपके लिए वह याद रखने को सहेज लिया है।',
                    'asm': 'মই সেইটো মনত ৰাখিবলৈ ৰাখিলোঁ।',
                    'ben': 'আমি সেটি মনে রাখার জন্য রেখে দিয়েছি।'}.get(
                        language, 'I have saved that reminder for you.')
        if data.get('started'):
            return {'eng': 'Starting your game now.', 'hin': 'आपका खेल शुरू कर रही हूँ।',
                    'asm': 'আপোনাৰ খেল আৰম্ভ কৰিছোঁ।',
                    'ben': 'আপনার খেলা শুরু করছি।'}.get(language, 'Starting your game now.')
        if data.get('reason') == 'NOT_A_TRUSTED_CONTACT':
            return refusal_text('unknown_number', language)
        return refusal_text('generic', language)

    # ------------------------------------------------------------------ #
    def _context(self, *, language: str, request_id: str, session_id: str,
                 principal: Principal) -> ToolContext:
        user = self.memory.repo.get_user(principal.user_id)
        return ToolContext(
            memory=self.memory, weather=self.weather, language=language,
            request_id=request_id, session_id=session_id,
            extras={'latitude': (user.latitude if user else None) or self.config.default_latitude,
                    'longitude': (user.longitude if user else None) or self.config.default_longitude,
                    'location': (user.location if user else None) or self.config.default_location})

    def _converse(self, message: str, *, session: ConversationSession, principal: Principal,
                  language: str, request_id: str, offline: bool) -> TurnOutcome:
        user = self.memory.repo.get_user(principal.user_id)
        system = build_system_prompt(language, offline=offline,
                                     user_name=user.display_name if user else None)
        specs: list[ToolSpec] = self.registry.specs()
        messages = self._history_messages(session)
        context = self._context(language=language, request_id=request_id,
                                session_id=session.session_id, principal=principal)

        tool_calls: list[ToolCall] = []
        tool_results: list[ToolResult] = []
        llm_latency = 0
        tool_latency = 0
        provider: str | None = None

        for _ in range(MAX_TOOL_ROUNDS):
            response = self.llm.generate(messages, system=system, tools=specs,
                                         request_id=request_id)
            llm_latency += response.latency_ms
            provider = response.provider

            if not response.tool_calls:
                text = response.text.strip() or _text_for(UNCLEAR, language, language)
                self._remember_subject(session, tool_results)
                return TurnOutcome(text=text, kind=(TurnKind.MEMORY if tool_results
                                                    else TurnKind.CONVERSATION),
                                   tool_calls=tool_calls, tool_results=tool_results,
                                   llm_provider=provider, llm_latency_ms=llm_latency,
                                   tool_latency_ms=tool_latency,
                                   fallback_used=self.llm.fallback_used)

            messages.append(Message('assistant', response.text,
                                    tool_calls=[{'name': call.name, 'arguments': call.arguments,
                                                 'call_id': call.call_id}
                                                for call in response.tool_calls]))

            for call in response.tool_calls:
                tool_calls.append(call)
                result = self.registry.execute(call, principal, context)
                tool_results.append(result)
                tool_latency += result.latency_ms

                if result.requires_confirmation:
                    tool = self.registry.get(call.name)
                    name = str((result.data or {}).get('arguments', {}).get('name') or
                               (result.data or {}).get('arguments', {}).get('text') or '')
                    pending = self.confirmations.create(
                        action=(tool.action if tool else call.name) or call.name,
                        language=language, tool_name=call.name,
                        arguments=(result.data or {}).get('arguments', {}), name=name)
                    session.set_pending(pending)
                    return TurnOutcome(text=pending.prompt, kind=TurnKind.CONFIRMATION,
                                       tool_calls=tool_calls, tool_results=tool_results,
                                       requires_confirmation=True, llm_provider=provider,
                                       llm_latency_ms=llm_latency, tool_latency_ms=tool_latency)

                payload = (result.data if result.ok else
                           {'error': result.error, 'error_code': result.error_code})
                messages.append(Message('tool', untrusted_block('tool_result', str(payload)),
                                        tool_name=call.name, tool_call_id=call.call_id))

        # Tool budget exhausted: answer with what we have rather than looping.
        self._remember_subject(session, tool_results)
        return TurnOutcome(text=_text_for(UNCLEAR, language, language),
                           kind=TurnKind.CONVERSATION, tool_calls=tool_calls,
                           tool_results=tool_results, llm_provider=provider,
                           llm_latency_ms=llm_latency, tool_latency_ms=tool_latency,
                           error_code='TOOL_ROUND_LIMIT')

    def _history_messages(self, session: ConversationSession) -> list[Message]:
        """Bounded history only: never the whole transcript."""
        turns = session.history(limit=session.max_turns * 2)
        return [Message('user' if turn.role == 'user' else 'assistant', turn.text)
                for turn in turns if turn.text]

    @staticmethod
    def _remember_subject(session: ConversationSession, results: list[ToolResult]) -> None:
        """Track the last person mentioned so 'she' can be resolved next turn."""
        for result in reversed(results):
            members = ((result.data or {}).get('members') or []) if result.data else []
            if members:
                session.last_subject = members[0].get('name')
                return

    # ------------------------------------------------------------------ #
    def _fallback(self, message: str, *, session: ConversationSession, principal: Principal,
                  language: str, request_id: str) -> TurnOutcome:
        context = self._context(language=language, request_id=request_id,
                                session_id=session.session_id, principal=principal)
        query = resolve_pronouns(message, session.last_subject)
        answer = self.responder.answer(query, principal=principal, context=context,
                                       language=language)
        if answer.answered and answer.data:
            members = (answer.data.get('members') or [])
            if members:
                session.last_subject = members[0].get('name')
        return TurnOutcome(text=answer.text, kind=TurnKind.FALLBACK, fallback_used=True,
                           error_code=None if answer.answered else 'NO_GENERAL_AI_AVAILABLE')

    # ------------------------------------------------------------------ #
    def _persist(self, session: ConversationSession, message: str, outcome: TurnOutcome,
                 metadata: TurnMetadata) -> None:
        try:
            self.memory.repo.ensure_session(session.session_id, session.user_id,
                                            session.language)
            self.memory.repo.add_turn(turn_id=uuid.uuid4().hex, session_id=session.session_id,
                                      user_id=session.user_id, role='user', text=message,
                                      language=session.language, kind='USER')
            self.memory.repo.add_turn(turn_id=uuid.uuid4().hex, session_id=session.session_id,
                                      user_id=session.user_id, role='assistant',
                                      text=outcome.text, language=session.language,
                                      kind=outcome.kind.value)
        except Exception as exc:  # persistence must never break a live turn
            log.warning('turn_persist_failed', fields={'request_id': metadata.request_id,
                                                       'error': type(exc).__name__})
        if self.telemetry is not None:
            try:
                self.telemetry.log(**metadata.model_dump(mode='json'))
            except Exception:
                pass
