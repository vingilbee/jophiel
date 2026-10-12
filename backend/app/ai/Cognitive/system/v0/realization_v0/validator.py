"""Independent candidate validation (``realization-validator-v0.3``).

The model's text and its self-reported alignments are untrusted input.  Every
check below is deterministic: exact anchors, numeric/unit/date integrity,
quotes, move punctuation and form, forbidden claims, required markers, style
bounds, and final integrity.  Detectors scan the *whole* text independently of
the alignments, so a candidate cannot hide an unplanned question, offer, or
claim by leaving it unaligned or by mislabeling a span.

This validator is never the generator and never a self-verdict.  Because
unrestricted semantic entailment cannot be proven, a passing candidate is only
**accepted for shadow review**: never truth-guaranteed or live-approved.  Known
limits (English-only lexical detectors, conservative false rejections, and no
general entailment) are recorded in the canonical README. The explicit
style_advisory-v0.1 policy records cosmetic warmth, endearment and exclamation
findings as warnings. All other checks remain blocking; strict-v0.1 retains
historical behavior for existing traces.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ..contracts.realization import (
    DIALOGUE_SOURCE_KINDS, MEMORY_SOURCE_KINDS, NON_ASSERTABLE_STATUSES, REALIZATION_UNITS, ArgumentValueType,
    CONVERSATIONAL_POLICIES, ArtifactPresentation, AuthorizedQuote, ContentAttribution, DialogueReference, DialogueReferenceKind, InquiryPrompt,
    ContentEpistemicStatus, ContentSourceKind, GapKind, GroundedProposition, GroundedQuestion, KnowledgeGap,
    Modality, Polarity, RealizationCandidateV0, RealizationFailurePhase, RealizationRequestV0,
    RealizationValidationResult, RealizationValidationPolicy, RealizationViolation, advisory_finding_allowed,
    ViolationCode as V, content_is_generic, valid_text, violation,
)
from .action_manifest import AlignmentKind, entry_for
from .errors import RealizationExecutionError

# ---------------------------------------------------------------------------
# Constraint enforcement registry
# ---------------------------------------------------------------------------

IMPLEMENTED_CHECKS: frozenset[str] = frozenset({
    "schema", "ownership", "move_coverage", "move_order", "text_coverage", "content_alignment",
    "anchor_presence", "numeric_integrity", "unit_integrity", "date_integrity", "proper_names",
    "quote_integrity", "uncertainty_marker", "polarity", "attribution", "modality", "conditionality",
    "gap_statement", "absence_not_negation", "memory_claims", "endorsement", "full_understanding",
    "tool_completion", "questions", "question_form", "offers", "commitments", "advice", "guidance_strength",
    "guarantees", "hidden_affect", "corrected_content", "play_frame", "structural_moves", "evidence_type",
    "style_markers", "endearments", "exclamation", "length", "internal_state", "control_characters",
    "copular_claims", "user_fault", "culpability", "causal_language", "general_boundary", "step_order",
})

# Every constraint code the compiler can emit, mapped to the checks that
# enforce it.  The compiler refuses to build a request containing a code that
# is absent here, so no inherited constraint can be silently unenforced.
CONSTRAINT_ENFORCEMENT: Mapping[str, tuple[str, ...]] = {
    # Action gate realization constraints.
    "forbid_completion_claim": ("tool_completion",),
    "forbid_factual_endorsement": ("endorsement",),
    "forbid_hidden_affect_assertion": ("hidden_affect",),
    "forbid_reasserting_corrected_content": ("corrected_content",),
    "forbid_unsupported_guarantee": ("guarantees", "commitments"),
    "preserve_factual_frame_boundary": ("play_frame", "copular_claims", "proper_names"),
    "preserve_uncertainty": ("uncertainty_marker",),
    # Global prohibitions.
    "introduce_unsupported_claim": ("copular_claims", "proper_names", "numeric_integrity", "quote_integrity",
                                    "text_coverage"),
    "answer_from_model_knowledge": ("copular_claims", "proper_names", "numeric_integrity", "gap_statement"),
    "change_numeric_value_or_unit": ("numeric_integrity", "unit_integrity", "date_integrity"),
    "drop_required_uncertainty": ("uncertainty_marker",),
    "change_attribution": ("attribution",),
    "claim_unobserved_tool_execution": ("tool_completion",),
    "claim_unrecorded_memory": ("memory_claims",),
    "add_unplanned_question": ("questions",),
    "add_unplanned_offer": ("offers",),
    "add_unplanned_commitment": ("commitments",),
    "add_unplanned_advice": ("advice",),
    "assert_hidden_affect": ("hidden_affect",),
    "expose_internal_cognitive_state": ("internal_state",),
    "reassert_corrected_content": ("corrected_content",),
    # Content-derived restrictions.
    "preserve_epistemic_status": ("uncertainty_marker", "attribution", "polarity", "endorsement"),
    "preserve_attribution": ("attribution",),
    "uncertainty_marker_required": ("uncertainty_marker",),
    "preserve_polarity": ("polarity",),
    "preserve_modality": ("modality",),
    "preserve_numeric_value": ("numeric_integrity", "unit_integrity", "date_integrity"),
    "no_gap_answer": ("gap_statement",),
    "absence_is_not_negation": ("absence_not_negation",),
    "preserve_quote_verbatim": ("quote_integrity",),
    "preserve_question_form": ("question_form",),
    "preserve_reference_scope": ("anchor_presence",),
    # Application safety.
    "no_operation_commitment": ("commitments", "offers"),
    # Manifest profile requirements.
    "replacement_required": ("anchor_presence", "content_alignment"),
    "basis_required": ("anchor_presence", "content_alignment"),
    "do_not_reassert_corrected_content": ("corrected_content",),
    "one_selected_question_form": ("question_form", "questions"),
    "no_embedded_answer": ("question_form", "copular_claims"),
    "no_agreement": ("endorsement",),
    "no_correctness_claim": ("endorsement",),
    "no_full_understanding_claim": ("full_understanding",),
    "scope_required": ("anchor_presence",),
    "no_user_fault_claim": ("user_fault",),
    "preserve_meaning": ("anchor_presence", "polarity", "numeric_integrity"),
    "no_new_claim": ("copular_claims", "proper_names", "numeric_integrity"),
    "preserve_tentativeness": ("uncertainty_marker",),
    "no_hidden_affect": ("hidden_affect",),
    "no_external_fact_upgrade": ("copular_claims", "endorsement"),
    "no_promise": ("commitments",),
    "no_guarantee": ("guarantees",),
    "involvement_or_impact_required": ("anchor_presence",),
    "no_invented_culpability": ("culpability",),
    "feasible_option_required": ("anchor_presence",),
    "preserve_guidance_strength": ("guidance_strength",),
    "preserve_commitment_level": ("commitments",),
    "no_completion_claim": ("tool_completion",),
    "preserve_dialogue_scope": ("anchor_presence", "text_coverage"),
    "no_unplanned_question": ("questions",),
    "no_unplanned_offer": ("offers",),
    "no_completion_claim_without_content": ("tool_completion",),
    "represented_failure_required": ("anchor_presence",),
    "preserve_repair_scope": ("anchor_presence",),
    "preserve_factual_frame": ("play_frame",),
    "no_new_factual_claim": ("copular_claims", "proper_names", "numeric_integrity"),
    # Manifest per-action requirements.
    "gap_yields_no_grounded_answer_only": ("gap_statement",),
    "causal_language_must_be_represented": ("causal_language",),
    "no_invented_example": ("anchor_presence", "text_coverage"),
    "bind_corrected_replacement_and_basis": ("anchor_presence", "corrected_content"),
    "qualify_only_bound_targets": ("uncertainty_marker",),
    "bind_unresolved_element": ("questions", "question_form"),
    "preserve_candidate_labels": ("question_form",),
    "preserve_tentative_status": ("endorsement",),
    "do_not_suggest_goal": ("advice",),
    "do_not_infer_preference": ("copular_claims",),
    "preserve_requested_evidence_type": ("evidence_type",),
    "no_implied_existing_evidence": ("evidence_type",),
    "bind_represented_stance": ("anchor_presence",),
    "never_assert_hidden_affect": ("hidden_affect",),
    "question_form_check_only": ("question_form",),
    "externalize_exact_interpretation": ("anchor_presence",),
    "bind_evaluated_object": ("anchor_presence",),
    "receipt_attention_only": ("endorsement", "full_understanding"),
    "disclose_own_status_not_user_defect": ("user_fault",),
    "no_scope_or_modality_change": ("modality", "polarity"),
    "tentative_observation_only": ("uncertainty_marker", "hidden_affect"),
    "no_unsupported_outcome_prediction": ("guarantees",),
    "validate_understandability_not_truth": ("endorsement",),
    "supported_achievement_only": ("anchor_presence",),
    "actual_involvement_only": ("culpability",),
    "preserve_conditionality": ("conditionality",),
    "keep_contribution_prospective": ("tool_completion",),
    "acceptance_is_not_completion": ("tool_completion",),
    "no_general_boundary": ("general_boundary",),
    "ordered_steps_required": ("step_order",),
    "forbid_completion_language": ("tool_completion",),
    "reviewed_opening_context_only": ("structural_moves",),
    "goal_completion_requires_content": ("tool_completion",),
    "grounded_inactive_topic_only": ("anchor_presence",),
    "no_unrepresented_prior_content": ("memory_claims",),
    "visibly_nonfactual_joke_material": ("play_frame",),
    "preserve_original_proposition": ("anchor_presence", "polarity"),
    # Validator v0.3: collaborative content and inquiries.
    "preserve_artifact_values": ("anchor_presence", "numeric_integrity", "unit_integrity"),
    "no_external_outcome_claim": ("tool_completion",),
    "artifact_operation_exact": ("anchor_presence", "content_alignment"),
    "inquiry_timing_preserved": ("move_order",),
}
if not {check for checks in CONSTRAINT_ENFORCEMENT.values() for check in checks} <= IMPLEMENTED_CHECKS:
    raise RuntimeError("a constraint maps to a check the validator does not implement")


# ---------------------------------------------------------------------------
# Lexical detectors (English; case-insensitive unless noted)
# ---------------------------------------------------------------------------

def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


_APOS = "['’]"
EMOTIONS = (r"sad|upset|angry|anxious|nervous|stressed|frustrated|worried|scared|excited|happy|thrilled|"
            r"disappointed|overwhelmed|hurt|lonely|afraid|down|heartbroken|devastated|ashamed|embarrassed|"
            r"hopeless|miserable|furious|terrified|jealous|guilty|proud|relieved")
GREETING = _rx(r"^\W*(hi|hello|hey|greetings|good (morning|afternoon|evening)|welcome)\b")
CLOSING = _rx(r"\b(bye|goodbye|good-bye|take care|talk (to you )?(soon|later)|see you|until next time|"
              r"have a (good|great|nice|lovely|wonderful) (day|evening|night|one|weekend))\b")
THANKS = _rx(r"\b(thank you|thanks|thank-you|much appreciated|cheers)\b")
CONGRATS = _rx(r"\b(congrat\w*|well done|great job|good job|nice work|great work|kudos|bravo)\b")
ACKNOWLEDGEMENT = _rx(rf"\b(got it|noted|understood|i see|i hear you|message received|gotcha|i{_APOS}ve got (it|that))\b")
APOLOGY = _rx(r"\b(sorry|apologi[sz]e\w*|apology|my mistake|my bad|forgive me|pardon me)\b")
REASSURANCE = _rx(rf"\b(don{_APOS}?t worry|do not worry|no need to worry|nothing to worry|not to worry|rest assured|"
                  rf"(it|everything|you|things)({_APOS}ll| will) (be )?(fine|ok|okay|alright|all right|work out)|"
                  rf"it{_APOS}?s going to be (fine|ok|okay|alright))\b")
PLAY = _rx(r"\b(haha+|ha ha|hehe|lol|lmao|rofl|just kidding|kidding|jk|joke|joking|pun)\b|[\U0001F602\U0001F923\U0001F606\U0001F604\U0001F61C\U0001F609]")
PLAY_FRAME = _rx(r"\b(just for fun|joking|kidding|jokingly|imagine|picture this|pretend|playful\w*|hypothetically|"
                 r"as if|metaphorically|so to speak|figuratively|in jest|tongue[- ]in[- ]cheek|haha+|ha ha)\b|"
                 r"\bif\b.{1,60}\bwere\b")
OFFER = _rx(rf"\b(want me to|would you like me to|shall i|should i|i can help|i could help|i{_APOS}?d be happy to|"
            rf"happy to help|glad to help|let me know if|feel free to ask|i can (do|take care|handle|look|check|"
            rf"draft|send|make|prepare|put|help|find|write|set)|i could (do|draft|send|make|prepare|put|take|help|"
            rf"find|write|set))\b")
COMMITMENT = _rx(rf"\b(i will|i{_APOS}ll|i promise|i{_APOS}m going to|i am going to|i commit|count on me|you have my word)\b")
PROMISE = _rx(r"\b(i promise|promise you|you have my word)\b")
FIRM_COMMITMENT = _rx(r"\b(definitely|certainly|absolutely|for sure|without fail|no matter what)\b")
ADVICE = _rx(rf"\b(you should|you ought to|i recommend|i{_APOS}?d recommend|i suggest|i{_APOS}?d suggest|you might want to|"
             rf"you may want to|you could try|it{_APOS}?s best to|it is best to|make sure to|be sure to|"
             rf"consider \w+ing|try to|i advise|my advice|why not)\b")
STRONG_GUIDANCE = _rx(r"\b(must|have to|need to|definitely should|should definitely|strongly (recommend|suggest|advise)|"
                      r"you really should|absolutely should|it is essential|it'?s essential)\b")
GUARANTEE = _rx(r"\b(guarantee\w*|definitely will|will definitely|for sure|certainly will|will certainly|"
                r"surely will|without a doubt|no doubt|always works|never fails|100 ?%|i assure you|assured)\b")
CERTAINTY_UPGRADE = _rx(r"\b(definitely|certainly|clearly|obviously|undoubtedly|for sure|without a doubt|"
                        r"no doubt|of course|absolutely)\b")
TOOL_COMPLETION = _rx(
    rf"\b(i{_APOS}?ve|i have|i) (just |already )?(sent|emailed|mailed|saved|stored|scheduled|booked|updated|logged|"
    r"created|uploaded|downloaded|deleted|archived|filed|submitted|posted|messaged|texted|called|summari[sz]ed|"
    r"analy[sz]ed|checked|searched|looked (it |that )?up|looked into|reviewed|opened|finished|completed|done|"
    r"fixed|set up|forwarded|added|recorded|calculated|measured|estimated|processed|handled|taken care)\b|"
    rf"\b(it{_APOS}?s|it is|that{_APOS}?s|that is|all) (done|sent|finished|complete|completed|taken care of|handled|sorted)\b"
)
# Claims about earlier sessions: licensed only by authorized long-term memory.
CROSS_SESSION_MEMORY = _rx(rf"\b(last time (you|we)|you told me last|you mentioned last time|"
                           rf"from our (last|previous|earlier) (chat|conversation|talk)|i{_APOS}?ve seen you)\b")
# Recall that is also true of this conversation: licensed by authorized memory
# or (realization-validator-v0.2) by delivered-dialogue content.
DIALOGUE_RECALL = _rx(rf"\b(i remember|i recall|i remembered|you told me (before|earlier)|"
                      rf"you mentioned (before|earlier)|as you said before|previously,? you|we talked about|"
                      rf"we discussed)\b")
NEVER_CLAIM = _rx(rf"\b(i{_APOS}?ve never|i have never|i never|never happened|did not happen|didn{_APOS}?t happen|"
                  rf"has never|have never|you never|you{_APOS}?ve never|you have never|there was never)\b")
# Speculation about another person's inner state is never licensed, even beside an attribution.
AFFECT_SPECULATIVE = _rx(
    rf"\b(you (must|probably|surely|clearly|really|obviously|likely) (feel|be feeling|be) \w+|"
    rf"you seem (to be )?(so |really |very )?({EMOTIONS})|"
    rf"(that|this|it) must (be|feel) (so |really |very )?(hard|difficult|frustrating|painful|stressful|scary|"
    rf"upsetting|exciting|tough|awful|overwhelming|lonely))"
)
# A plain report of the user's feeling is allowed only as an attributed restatement.
AFFECT_REPORT = _rx(
    rf"\b(you feel\b|you{_APOS}?re feeling|you are feeling|you{_APOS}?re (so |really |very |quite )?({EMOTIONS})\b|"
    rf"you are (so |really |very |quite )?({EMOTIONS})\b)"
)
AFFECT_SELF = _rx(
    rf"\b(i feel|i{_APOS}m feeling|i am feeling|i{_APOS}m (so |really |very |truly )?(happy|glad|excited|sad|thrilled|"
    rf"delighted|worried|upset|proud|touched|moved|nervous|heartbroken|sorry to hear)|i am (so |really |very |truly )?"
    rf"(happy|glad|excited|sad|thrilled|delighted|worried|upset|proud|touched|moved|nervous|heartbroken)|"
    rf"i love\b|i{_APOS}?d love\b|makes me (happy|sad|glad|proud))"
)
ENDORSEMENT = _rx(rf"\b((that{_APOS}?s|that is|you{_APOS}?re|you are|it{_APOS}?s|it is) (absolutely |totally |completely |"
                  rf"quite |entirely |so )?(right|correct|true|accurate)|exactly right|i agree|agreed|good point|fair point|"
                  rf"spot on|i concur|you{_APOS}?re not wrong)\b")
FULL_UNDERSTANDING = _rx(r"\b(i (completely|totally|fully|perfectly|entirely) understand|i understand (completely|"
                         r"perfectly|fully|exactly|entirely)|makes (perfect|total|complete) sense|i know exactly|"
                         r"i get it completely|i fully get)\b")
UNCERTAINTY = _rx(rf"\b(about|approximately|approx|roughly|around|nearly|almost|maybe|perhaps|possibly|probably|"
                  rf"likely|unlikely|might|may|could|seems?|appears?|i think|i believe|not (entirely |completely |"
                  rf"totally |fully )?(sure|certain)|uncertain|unclear|tentative\w*|"
                  rf"if i understand|if i{_APOS}?m reading|sounds like|it looks like|my understanding is|"
                  rf"not confirmed|unconfirmed|reportedly|apparently|suggests?|if i{_APOS}?m right|as far as i can tell)\b|~")
NEGATION = _rx(rf"\b(not|no|never|none|nothing|neither|nor|cannot|without)\b|n{_APOS}t\b")
ATTRIBUTION_USER = _rx(rf"\b(you said|you say|you mentioned|you told me|you noted|you shared|you wrote|you described|"
                       rf"you{_APOS}?ve said|you{_APOS}?ve mentioned|you indicated|you reported|you asked|you stated|"
                       rf"according to you|from what you said|as you put it|in your words|your (message|note) says|"
                       rf"you{_APOS}?re saying|you are saying|you{_APOS}?ve shared|you let me know|you expressed|"
                       rf"you suggested|you proposed|your suggestion|you requested|your request|you wanted)\b")
ATTRIBUTION_THIRD = _rx(r"\b(according to|reported|reports|reportedly|said|says|stated|states|claims|claimed|"
                        r"as noted by|the source|per)\b")
_SELF_REPORT_KINDS = frozenset({DialogueReferenceKind.EXPERIENCE, DialogueReferenceKind.CONCERN})
SECOND_PERSON = _rx(rf"\b(you|your|yours|you{_APOS}?re|you{_APOS}?ve|you{_APOS}?d)\b")
GAP_STATEMENT = _rx(rf"\b(don{_APOS}?t have|do not have|no grounded|can{_APOS}?t confirm|cannot confirm|can{_APOS}?t verify|"
                    rf"cannot verify|not able to (confirm|verify)|unable to (confirm|verify|answer)|no record|no memory|"
                    rf"not sure|don{_APOS}?t know|do not know|no information|i lack|without a grounded|i have no|"
                    rf"i{_APOS}?m not able to|not in what i have)\b")
# A ``not_mentioned_in_conversation`` gap (registry v1.5) is stated as what the
# user has not said; only that gap kind accepts this wording.
MENTION_GAP_STATEMENT = _rx(rf"\b((didn{_APOS}?t|did not|haven{_APOS}?t|have not|hasn{_APOS}?t) (mention|mentioned|say|said|tell|told)|"
                            rf"not mentioned|not something you{_APOS}?ve (said|mentioned))\b")
CORRECTION_MARKER = _rx(rf"\b(not|rather than|instead of|instead|correction|incorrect|wrong|mistaken|earlier i said|"
                        rf"i said earlier|previously|actually|should have been|was wrong|misstated|to correct|"
                        rf"isn{_APOS}?t|wasn{_APOS}?t|no longer)\b")
CONDITIONAL = _rx(r"\b(if|would|could|might|may|depending|in case|provided|assuming|unless|should you|when)\b")
USER_FAULT = _rx(rf"\b(you (were|are) (unclear|confusing|vague|wrong|mistaken)|your (message|question|request) "
                 rf"(was|is) (unclear|confusing|vague|wrong)|you didn{_APOS}?t (explain|say|make|specify)|you forgot|"
                 rf"you failed|your fault)\b")
CULPABILITY = _rx(rf"\b(my fault|i was wrong|i messed up|i screwed up|i{_APOS}?m to blame|i caused|i ruined|i broke|"
                  rf"entirely on me|i take full responsibility)\b")
CAUSAL = _rx(r"\b(because|due to|caus(e|es|ed|ing)|leads? to|led to|results? in|resulted in|therefore|thus|hence|"
             r"so that|as a result|that'?s why|which means)\b")
GENERAL_BOUNDARY = _rx(rf"\b(i (never|don{_APOS}?t ever|won{_APOS}?t ever|can never|do not ever)|as a rule|"
                       rf"i never do|i don{_APOS}?t do that|i do not do that|that{_APOS}?s not something i (ever )?do)\b")
ENDEARMENT = _rx(r"\b(dear|dearest|sweetie|sweetheart|honey|hun|babe|baby|buddy|pal|darling|my friend|mate)\b")
WARMTH_MARKER = _rx(r"\b(glad|happy to|great|lovely|wonderful|nice|appreciate\w*|of course|absolutely|no problem|"
                    r"sure thing|my pleasure|delighted|awesome|fantastic|brilliant)\b|[\U0001F60A\U0001F642☺]|:\)")
INTERNAL_TERMS = _rx(r"\b(action plan|action[_ ]key|move[_ ]ref|content[_ ]id|content item|content ref|cognitive|"
                     r"appraisal|dynamic state|derived control|realization|constraint code|payload|system prompt|"
                     r"my instructions|internal state|interpretation hypothesis|goal snapshot|shadow mode|"
                     r"delivery profile|style marker|json)\b")
SNAKE_CASE = re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b")
YES_NO_AUXILIARY = _rx(r"^\W*(is|are|am|was|were|do|does|did|can|could|would|will|have|has|had|should|shall|may|"
                       r"might|must|isn't|aren't|don't|doesn't|didn't|won't|wouldn't|haven't)\b")
EVIDENCE_SURFACES: Mapping[str, tuple[str, ...]] = {
    "document": ("document", "file", "pdf", "doc", "attachment", "copy"),
    "record": ("record", "log", "entry", "receipt", "statement", "history"),
    "observation": ("observ", "see", "saw", "notice", "screenshot", "photo", "picture", "look"),
    "source": ("source", "link", "reference", "citation", "url", "where"),
}
IMPLIED_EVIDENCE = _rx(r"\b(the (document|file|record|receipt|screenshot|source) you have|your (document|file|record|"
                       r"receipt|screenshot|source) (shows|says|proves|confirms))\b")
CONNECTIVES = frozenset({"and", "also", "but", "so", "then", "plus", "or", "meanwhile", "anyway"})
COPULA = re.compile(r"\b([A-Za-z][A-Za-z'’-]*)\s+(is|are|was|were)\b", re.IGNORECASE)
COPULA_SUBJECTS = frozenset({
    "i", "you", "we", "it", "that", "this", "there", "here", "which", "what", "who", "whom", "how", "when",
    "where", "why", "he", "she", "they", "one", "message", "question", "request", "point", "idea", "plan",
    "note", "part", "thing", "detail", "details", "answer", "information", "that's", "it's", "what's",
    "your", "my", "our", "these", "those", "step", "option", "topic", "conversation", "chat",
})
NUMBER_WORDS: Mapping[str, int] = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000, "million": 1_000_000,
    "billion": 1_000_000_000, "dozen": 12,
}
NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.])\d+(?:\.\d+)?(?![\w])")
NUMBER_WORD = _rx(r"\b(" + "|".join(NUMBER_WORDS) + r")\b")
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")
CAPITALIZED = re.compile(r"\b[A-Z][A-Za-z'’-]*\b")
QUOTED = re.compile(r"[\"“]([^\"“”]+)[\"”]")
SENTENCE_STARTERS = frozenset("""
a about absolutely according actually after again all also although always an and another any anything
apologies are as at based be because before being both but by can could did do does don't each either
even every everything first for from further given glad good great had has have having he her here hey
hi his how however i if in instead is it it's its just kindly let let's like likely looking many may
maybe meanwhile might more most much my next no none not nothing noted now of okay ok on once one only
or other our perhaps please possibly probably quick quite rather really right roughly said same second
see she should similarly since so some something sorry still such sure thanks thank that that's the
their them then there there's these they this those though thus to together too understood unfortunately
until up us very was we well were what what's when where whether which while who why will with within
without would yes yet you your you're you've you'd approximately around nearly almost hello got
correction earlier previously sounds seems it'll could've whatever welcome bye goodbye take talk tell
share give send happy nice anyone anybody everyone someone somebody nobody whichever whose whom hmm am
""".split())
# segmented_response-v0.2: ordinary imperative/suggestion openers. Capitalized
# only because they open a sentence ("Try a stir-fry.", "Cook the rice..."),
# never names. Closed and reviewable; mid-sentence capitals are still checked.
SUGGESTION_STARTERS = frozenset("""
try here's here’s cook add serve start stir heat mix combine top use make consider pair swap enjoy keep prep
boil bake grill roast simmer season chop slice toss sprinkle finish pick choose go grab skip aim opt check
note remember plan prepare cut drain warm fry saute sauté blend whisk spread fill layer place put set leave
pop throw stick lean double half halve cover rinse wash dice mince marinate garnish drizzle squeeze melt
another option alternatively optionally otherwise for if want need sounds start begin finally lastly
""".split())

_DIGIT_BOUNDARY = re.compile(r"\d")


@dataclass(frozen=True)
class _Sentence:
    start: int
    end: int
    text: str

    @property
    def is_question(self) -> bool:
        return self.text.rstrip().rstrip("\"'”’)").endswith("?")


def sentences(text: str) -> tuple[_Sentence, ...]:
    """Split on ., !, ? followed by whitespace/end (never inside decimals) and on newlines."""

    out: list[_Sentence] = []
    start = 0
    length = len(text)
    index = 0
    while index < length:
        char = text[index]
        boundary = False
        if char == "\n":
            boundary = True
        elif char in ".!?":
            follow = index + 1
            while follow < length and text[follow] in ".!?\"'”’)":
                follow += 1
            if follow >= length or text[follow].isspace():
                index = follow - 1
                boundary = True
        if boundary:
            end = index + 1
            if text[start:end].strip():
                lead = start + (len(text[start:end]) - len(text[start:end].lstrip()))
                out.append(_Sentence(lead, end, text[lead:end].strip()))
            start = end
        index += 1
    if text[start:].strip():
        lead = start + (len(text[start:]) - len(text[start:].lstrip()))
        out.append(_Sentence(lead, length, text[lead:].strip()))
    return tuple(out)


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("’", "'")).strip().casefold()


_WORDS = re.compile(r"[^\W_]+(?:'[^\W_]+)?")
_ANCHOR_FUNCTION_WORDS = frozenset("""
a an the and or of to for in on at by with about from as is are be do does did you your yours i me my
which what that this these those it its mean means meant
mostly because just really actually so also very lately been being have has had am was were
""".split())


def contains_anchor(haystack: str, anchor: str) -> bool:
    needle = _norm(anchor)
    if not needle:
        return False
    hay = _norm(haystack)
    pattern = (r"(?<!\w)" if needle[0].isalnum() else "") + re.escape(needle) + (r"(?!\w)" if needle[-1].isalnum() else "")
    return re.search(pattern, hay) is not None


_ADDRESSED = {"i": "you", "me": "you", "my": "your", "mine": "yours", "myself": "yourself",
              "i'm": "you're", "i’m": "you’re", "i've": "you've", "i’ve": "you’ve", "i'd": "you'd", "i’d": "you’d",
              "i'll": "you'll", "i’ll": "you’ll"}
_FIRST_PERSON = re.compile(r"\b(?:i[’']m|i[’']ve|i[’']d|i[’']ll|myself|mine|my|me|i)\b", re.IGNORECASE)


def addressed(anchor: str) -> str:
    """The user's own words as said back to them: "me" becomes "you" (conversational-v3 answer matching)."""

    return _FIRST_PERSON.sub(lambda match: _ADDRESSED[match.group().casefold()], anchor)


def outside_anchors(text: str, anchors: Iterable[str]) -> str:
    """``text`` with every authorized anchor occurrence blanked, so content words never count as markers."""

    out = text
    for anchor in sorted({a for a in anchors if a.strip()}, key=len, reverse=True):
        out = re.sub(re.escape(anchor), lambda match: " " * len(match.group()), out, flags=re.IGNORECASE)
    return out


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def numbers_in(text: str) -> list[tuple[int, int, Decimal]]:
    found = [(match.start(), match.end(), _decimal(match.group())) for match in NUMBER.finditer(text)]
    found += [(match.start(), match.end(), Decimal(NUMBER_WORDS[match.group().lower()]))
              for match in NUMBER_WORD.finditer(text)]
    return sorted(item for item in found if item[2] is not None)


_UNIT_SURFACES = sorted(
    ((surface, unit) for unit, surfaces in REALIZATION_UNITS.items() for surface in surfaces),
    key=lambda item: -len(item[0]),
)


def unit_after(text: str, end: int) -> str | None:
    rest = text[end:end + 16].lower()
    for surface, unit in _UNIT_SURFACES:
        if surface in {"$", "€"}:
            continue
        stripped = rest.lstrip()
        if stripped.startswith(surface) and (len(stripped) == len(surface) or not stripped[len(surface)].isalnum()):
            if surface == "%" or rest[: len(rest) - len(stripped)] in {"", " "}:
                return unit
    return None


def unit_before(text: str, start: int) -> str | None:
    before = text[max(0, start - 2):start].rstrip()
    if before.endswith("$"):
        return "dollar"
    if before.endswith("€"):
        return "euro"
    return None


def date_surfaces(iso: str) -> tuple[str, ...]:
    year, month, day = iso.split("-")
    name = MONTHS[int(month) - 1].capitalize()
    d = str(int(day))
    return (iso, f"{name} {d}", f"{d} {name}", f"{name} {d}st", f"{name} {d}nd", f"{name} {d}rd", f"{name} {d}th",
            f"{name} {d}, {year}", f"{d} {name} {year}")


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class RealizationValidatorV0:
    """Deterministic validation of one untrusted candidate against its request."""

    def __init__(self, *, policy: RealizationValidationPolicy = "strict-v0.1") -> None:
        if policy not in {"strict-v0.1", "style_advisory-v0.1", "segmented_response-v0.1", "segmented_response-v0.2",
                          *CONVERSATIONAL_POLICIES}:
            raise ValueError("unsupported realization validation policy")
        self.policy = policy

    def schema_failure(self, request: RealizationRequestV0, attempt_number: int) -> RealizationValidationResult:
        """The result for output that never parsed into the fixed candidate contract."""

        return RealizationValidationResult(
            request_ref=request.request_digest, candidate_ref=None, attempt_number=attempt_number,
            validation_policy=self.policy,
            status="rejected", violations=(violation(V.OUTPUT_SCHEMA_INVALID, "candidate"),),
        )

    def validate(self, request: RealizationRequestV0, candidate: RealizationCandidateV0) -> RealizationValidationResult:
        found: dict[tuple, RealizationViolation] = {}

        def flag(code: V, subject: str) -> None:
            item = violation(code, subject)
            found[item.sort_key] = item

        _Run(request, candidate, flag, policy=self.policy).run()
        findings = tuple(found[key] for key in sorted(found))
        # Policy-approved advisory findings remain inspectable; they never
        # relax grounding, epistemic fidelity, material acts, or length limits.
        warnings = tuple(item for item in findings if advisory_finding_allowed(self.policy, item))
        violations = tuple(item for item in findings if item not in warnings)
        return RealizationValidationResult(
            request_ref=request.request_digest, candidate_ref=candidate.candidate_id,
            attempt_number=candidate.attempt_number,
            validation_policy=self.policy, warnings=warnings,
            status="rejected" if violations else "accepted_for_shadow_review", violations=violations,
        )


class _Run:
    """One validation pass.  Subjects are refs or positional labels, never candidate text."""

    def __init__(self, request: RealizationRequestV0, candidate: RealizationCandidateV0, flag, *, policy="strict-v0.1") -> None:
        self.policy=policy
        self.conversational = policy in CONVERSATIONAL_POLICIES
        self.request, self.candidate, self.flag = request, candidate, flag
        self.entries = {move.move_ref: entry_for(move.action_key, move.definition_version)
                        for move in request.action_plan.moves}
        if any(entry is None for entry in self.entries.values()):
            raise RealizationExecutionError(RealizationFailurePhase.CANDIDATE_VALIDATION, "request_action_unrealizable")
        self.text = candidate.text
        self.sentences = sentences(self.text)
        # The user's authorized verbatim words are not Columbina's acts: lexical
        # act detectors scan the text with those quotes blanked.
        quotes = [item.quote_text for item in request.content_items if isinstance(item, AuthorizedQuote)]
        if policy in CONVERSATIONAL_POLICIES:
            # Quoting the user's own current words is not an act either.
            user_words = request.linguistic_context.text.casefold()
            quotes += [match.group(1) for match in QUOTED.finditer(self.text)
                       if match.group(1).strip() and match.group(1).strip().casefold() in user_words]
        self.act_text = outside_anchors(self.text, quotes)
        self.act_sentences = tuple(_Sentence(item.start, item.end, self.act_text[item.start:item.end])
                                   for item in self.sentences)
        self.moves = request.action_plan.moves
        self.items = {item.content_id: item for item in request.content_items}
        self.bound = {binding.move_ref: binding for binding in request.move_bindings}
        self.spans: dict[str, tuple[int, int]] = {}
        self.keys = {move.action_key for move in self.moves}
        self.constraint_codes = {item.code for item in request.constraints}
        anchors: list[str] = []
        for item in request.content_items:
            anchors.extend(item.anchors)
        self.anchor_text = "\n".join(anchors)
        self.allowed_numbers = self._allowed_numbers()
        self.allowed_units = {
            arg.unit for item in request.content_items if isinstance(item, (GroundedProposition, ArtifactPresentation))
            for arg in item.arguments if arg.unit
        }
        # Validator v0.3: deliver moves and the wording authority their items license.
        self.deliveries = {move.move_ref: [item for item in self.items_of(move.move_ref)
                                           if isinstance(item, ArtifactPresentation)]
                           for move in self.moves if move.action_key == "collaborate.deliver_artifact"}

    # -- helpers -----------------------------------------------------------

    def span_text(self, move_ref: str) -> str | None:
        span = self.spans.get(move_ref)
        return None if span is None else self.text[span[0]:span[1]]

    def sentence_label(self, sentence: _Sentence) -> str:
        position = next(index for index, item in enumerate(self.sentences) if item.start == sentence.start)
        return f"text:sentence-{position + 1}"

    def move_of(self, position: int) -> str | None:
        for ref, (start, end) in self.spans.items():
            if start <= position < end:
                return ref
        return None

    def items_of(self, move_ref: str) -> list:
        return [self.items[ref] for ref in self.bound[move_ref].bound_content_refs]

    def selected(self, *keys: str) -> bool:
        return bool(self.keys & set(keys))

    def _allowed_numbers(self) -> set[Decimal]:
        allowed: set[Decimal] = set()
        for item in self.request.content_items:
            if isinstance(item, (GroundedProposition, ArtifactPresentation)):
                for arg in item.arguments:
                    if arg.value_type in {ArgumentValueType.DECIMAL, ArgumentValueType.INTEGER}:
                        allowed.add(Decimal(arg.value))
                    elif arg.value_type == ArgumentValueType.DATE:
                        year, month, day = arg.value.split("-")
                        allowed.update({Decimal(year), Decimal(int(month)), Decimal(int(day))})
            if isinstance(item, GroundedQuestion) and item.scale_min is not None:
                allowed.update({Decimal(item.scale_min), Decimal(item.scale_max)})
        for move in self.moves:
            for key in ("scale_min", "scale_max"):
                value = move.parameters.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    allowed.add(Decimal(value))
        allowed.update(value for _, _, value in numbers_in(self.anchor_text))
        return allowed

    # -- run ---------------------------------------------------------------

    def run(self) -> None:
        self.ownership()
        self.alignment()
        self.text_coverage()
        self.content_grounding()
        self.global_integrity()
        self.per_move()
        self.structural_moves()
        self.style_and_length()
        if self.policy == "conversational-v3":
            self.answer_fidelity()

    def ownership(self) -> None:
        if self.candidate.request_ref != self.request.request_digest:
            self.flag(V.REQUEST_OWNERSHIP_MISMATCH, "candidate")

    def alignment(self) -> None:
        order = tuple(move.move_ref for move in self.moves)
        known = set(order)
        counts: dict[str, list] = {ref: [] for ref in order}
        for item in self.candidate.move_alignments:
            if item.ref not in known:
                self.flag(V.ALIGNMENT_INVALID, "alignment:unknown-move")
            else:
                counts[item.ref].append(item)
        for ref in self.candidate.omitted_move_refs:
            if ref in known:
                self.flag(V.REQUIRED_MOVE_MISSING, ref)
            else:
                self.flag(V.ALIGNMENT_INVALID, "alignment:unknown-move")
        for ref, found in counts.items():
            if not found:
                self.flag(V.REQUIRED_MOVE_MISSING, ref)
            elif len(found) > 1:
                self.flag(V.ALIGNMENT_INVALID, ref)
            else:
                self.spans[ref] = (found[0].start, found[0].end)
        placed = sorted(self.spans, key=lambda ref: self.spans[ref])
        if tuple(ref for ref in order if ref in self.spans) != tuple(placed):
            self.flag(V.MOVE_ORDER_CHANGED, "move_order")
        for left, right in zip(placed, placed[1:]):
            if self.spans[left][1] > self.spans[right][0]:
                compatible={'manage_dialogue.greet','give_feedback.acknowledge'}
                actions={m.action_key for m in self.moves if m.move_ref in {left,right}}
                shared=(self.policy in {'segmented_response-v0.1','segmented_response-v0.2'} and self.spans[left]==self.spans[right]
                        and len(actions)==2 and actions<=compatible)
                if not shared:self.flag(V.ALIGNMENT_INVALID, right)
        for item in self.candidate.content_alignments:
            if item.ref not in self.items:
                self.flag(V.ALIGNMENT_INVALID, "alignment:unknown-content")

    def text_coverage(self) -> None:
        """Text outside every move span may only be whitespace, punctuation, or connectives."""

        residue = list(self.text)
        for start, end in self.spans.values():
            residue[start:end] = [" "] * (end - start)
        words = re.findall(r"[^\W_]+(?:['’][^\W_]+)?", "".join(residue))
        if any(word.casefold() not in CONNECTIVES for word in words):
            self.flag(V.UNPLANNED_MOVE_DETECTED, "text:unaligned")

    def content_grounding(self) -> None:
        for move in self.moves:
            span = self.spans.get(move.move_ref)
            for ref in self.bound[move.move_ref].bound_content_refs:
                aligned = any(item.ref == ref and span is not None and span[0] <= item.start and item.end <= span[1]
                              for item in self.candidate.content_alignments) or (
                    self.conversational and span is not None and ref in self.items
                    and (content_is_generic(self.items[ref])  # structural: realized by its move
                         or any(self.anchor_in(self.text[span[0]:span[1]], anchor) for anchor in self.items[ref].anchors)))
                if not aligned and not any(
                    item.ref == ref and self.spans.get(other.move_ref) is not None
                    and self.spans[other.move_ref][0] <= item.start and item.end <= self.spans[other.move_ref][1]
                    and ref in self.bound[other.move_ref].bound_content_refs
                    for item in self.candidate.content_alignments for other in self.moves
                ):
                    self.flag(V.CONTENT_ALIGNMENT_MISSING, ref)
            if span is None:
                continue
            text = self.text[span[0]:span[1]]
            for item in self.items_of(move.move_ref):
                if content_is_generic(item):
                    continue
                # Every binding move refers to the item by at least one authorized anchor.
                if isinstance(item, AuthorizedQuote):
                    if item.quote_text not in text:
                        self.flag(V.QUOTE_ALTERED, item.content_id)
                elif not any(self.anchor_in(text, anchor) for anchor in item.anchors):
                    self.flag(V.CONTENT_ALIGNMENT_MISSING, item.content_id)
        # Every assertable proposition is fully expressed (all values) in at least one binding move.
        for item in self.request.content_items:
            if not isinstance(item, GroundedProposition) or item.epistemic_status in NON_ASSERTABLE_STATUSES:
                continue
            spans = [self.text[self.spans[move.move_ref][0]:self.spans[move.move_ref][1]] for move in self.moves
                     if move.move_ref in self.spans and item.content_id in self.bound[move.move_ref].bound_content_refs]
            if not spans:
                continue
            outcomes = [self.argument_violations(item, text) for text in spans]
            if all(outcomes):
                for code in min(outcomes, key=len):
                    self.flag(code, item.content_id)
        # Every artifact value a presented item carries appears exactly (no rounding, no change).
        for item in self.request.content_items:
            if not isinstance(item, ArtifactPresentation) or not item.arguments:
                continue
            spans = [self.text[self.spans[move.move_ref][0]:self.spans[move.move_ref][1]] for move in self.moves
                     if move.move_ref in self.spans and item.content_id in self.bound[move.move_ref].bound_content_refs]
            if spans and not any(all(any(value == Decimal(arg.value) for _, _, value in numbers_in(text))
                                     for arg in item.arguments) for text in spans):
                self.flag(V.NUMERIC_VALUE_CHANGED, item.content_id)

    def argument_violations(self, item: GroundedProposition, text: str) -> list[V]:
        """Why ``text`` fails to carry every value of ``item`` exactly (empty when it does)."""

        out: list[V] = []
        if not any(contains_anchor(text, anchor) for anchor in (item.subject.surface_anchor, *item.surface_anchors)):
            out.append(V.CONTENT_ALIGNMENT_MISSING)
        span_numbers = [value for _, _, value in numbers_in(text)]
        for arg in item.arguments:
            if arg.value_type in {ArgumentValueType.DECIMAL, ArgumentValueType.INTEGER}:
                matches = [(s, e, value) for s, e, value in numbers_in(text) if value == Decimal(arg.value)]
                if not matches:
                    out.append(V.NUMERIC_VALUE_CHANGED if span_numbers else V.CONTENT_ALIGNMENT_MISSING)
                elif arg.unit is not None:
                    units = {unit_after(text, e) or unit_before(text, s) for s, e, _ in matches}
                    if arg.unit not in units:
                        out.append(V.UNIT_CHANGED if units - {None} else V.CONTENT_ALIGNMENT_MISSING)
            elif arg.value_type == ArgumentValueType.DATE:
                if not any(contains_anchor(text, surface) for surface in date_surfaces(arg.value)):
                    out.append(V.NUMERIC_VALUE_CHANGED if span_numbers else V.CONTENT_ALIGNMENT_MISSING)
            elif arg.surface_anchor is not None and not contains_anchor(text, arg.surface_anchor):
                out.append(V.CONTENT_ALIGNMENT_MISSING)
        return out

    # -- global integrity ---------------------------------------------------

    def global_integrity(self) -> None:
        text = self.text
        # Numbers, units, and dates must come from authorized content.
        numeric_content = any(
            isinstance(item, GroundedProposition) and any(arg.value_type in {
                ArgumentValueType.DECIMAL, ArgumentValueType.INTEGER, ArgumentValueType.DATE} for arg in item.arguments)
            for item in self.request.content_items
        )
        for start, end, value in numbers_in(text):
            if value not in self.allowed_numbers:
                self.flag(V.NUMERIC_VALUE_CHANGED if numeric_content else V.UNSUPPORTED_CLAIM_DETECTED,
                          f"text:number-{self._ordinal(start)}")
            unit = unit_after(text, end) or unit_before(text, start)
            if unit is not None and unit not in self.allowed_units:
                self.flag(V.UNIT_CHANGED, f"text:number-{self._ordinal(start)}")
        # Proper names (and month/weekday names) must be contained in authorized anchors.
        allowed_names = self.anchor_text + "\n" + "\n".join(
            surface for item in self.request.content_items if isinstance(item, GroundedProposition)
            for arg in item.arguments if arg.value_type == ArgumentValueType.DATE for surface in date_surfaces(arg.value)
        )
        for sentence in self.sentences:
            first = re.search(r"[A-Za-z][A-Za-z'’-]*", sentence.text)
            for match in CAPITALIZED.finditer(sentence.text):
                word = match.group()
                if word == "I" or re.fullmatch(r"I['’](m|ll|ve|d)", word):
                    continue
                if first is not None and match.start() == first.start() and (_norm(word) in SENTENCE_STARTERS or (
                    self.policy in {"segmented_response-v0.2", *CONVERSATIONAL_POLICIES} and _norm(word) in SUGGESTION_STARTERS
                )):
                    continue
                if first is not None and match.start() == first.start() and re.search(
                    r"(?<!\w)" + re.escape(word) + r"(?!\w)", allowed_names, re.IGNORECASE,
                ):
                    # v0.3: an authorized anchor word capitalized only because it
                    # opens the sentence is not a new proper name.
                    continue
                if not re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", allowed_names):
                    self.flag(V.UNSUPPORTED_CLAIM_DETECTED, self.sentence_label(sentence))
        # Quotes must be authorized verbatim quotes.
        quotes = [item for item in self.request.content_items if isinstance(item, AuthorizedQuote)]
        user_words = self.request.linguistic_context.text if self.conversational else ""
        for match in QUOTED.finditer(text):
            if self.conversational and match.group(1).strip() and match.group(1).strip().casefold() in user_words.casefold():
                continue  # quoting the user's own current words asserts nothing new
            if not any(match.group(1) == item.quote_text for item in quotes):
                self.flag(V.QUOTE_ALTERED if quotes else V.UNSUPPORTED_CLAIM_DETECTED, f"text:quote-{match.start()}")
        # Claims that no V0 action licenses.
        tool_results = any(item.source_kind == ContentSourceKind.FINALIZED_TOOL_RESULT
                           for item in self.request.content_items)
        if not tool_results:
            for match in TOOL_COMPLETION.finditer(self.act_text):
                if not self.change_licensed(match.start()):
                    self.flag(V.FALSE_TOOL_COMPLETION_CLAIM, "text")
                    break
        memories = any(item.source_kind in MEMORY_SOURCE_KINDS for item in self.request.content_items)
        dialogue = any(item.source_kind in DIALOGUE_SOURCE_KINDS for item in self.request.content_items)
        if (CROSS_SESSION_MEMORY.search(self.act_text) and not memories) or (
            DIALOGUE_RECALL.search(self.act_text) and not (memories or dialogue)
        ):
            self.flag(V.UNRECORDED_MEMORY_CLAIMED, "text")
        negative_anchors = [anchor for item in self.request.content_items
                            if isinstance(item, GroundedProposition) and item.polarity == Polarity.NEGATIVE
                            for anchor in item.anchors]
        for sentence in self.act_sentences:
            if NEVER_CLAIM.search(sentence.text) and not any(contains_anchor(sentence.text, a) for a in negative_anchors):
                self.flag(V.UNRECORDED_MEMORY_CLAIMED, self.sentence_label(sentence))
            if AFFECT_SELF.search(sentence.text):
                self.flag(V.HIDDEN_AFFECT_ASSERTED, self.sentence_label(sentence))
            if AFFECT_SPECULATIVE.search(sentence.text):
                self.flag(V.HIDDEN_AFFECT_ASSERTED, self.sentence_label(sentence))
            report = AFFECT_REPORT.search(sentence.text)
            if report and not any(marker.end() <= report.start()
                                  for marker in ATTRIBUTION_USER.finditer(sentence.text)):
                self.flag(V.HIDDEN_AFFECT_ASSERTED, self.sentence_label(sentence))
            if ENDORSEMENT.search(sentence.text):
                self.flag(V.FACTUAL_ENDORSEMENT_DETECTED, self.sentence_label(sentence))
            if FULL_UNDERSTANDING.search(sentence.text) or USER_FAULT.search(sentence.text) or (
                GENERAL_BOUNDARY.search(sentence.text)
            ):
                self.flag(V.UNSUPPORTED_CLAIM_DETECTED, self.sentence_label(sentence))
            if CULPABILITY.search(sentence.text):
                self.flag(V.UNSUPPORTED_CLAIM_DETECTED, self.sentence_label(sentence))
            if GUARANTEE.search(sentence.text):
                self.flag(V.UNSUPPORTED_GUARANTEE, self.sentence_label(sentence))
            if PROMISE.search(sentence.text):
                self.flag(V.UNPLANNED_COMMITMENT, self.sentence_label(sentence))
            if IMPLIED_EVIDENCE.search(sentence.text):
                self.flag(V.UNSUPPORTED_CLAIM_DETECTED, self.sentence_label(sentence))
            if CAUSAL.search(sentence.text):
                owner = self.move_of(sentence.start)
                owner_key = next((m.action_key for m in self.moves if m.move_ref == owner), None)
                licensed = owner_key == "provide_information.explain" or any(
                    item.licenses_causal and self.mentions(sentence.text, item)
                    for item in self.deliveries.get(owner, ())
                )
                if not licensed:
                    self.flag(V.UNSUPPORTED_CLAIM_DETECTED, self.sentence_label(sentence))
            self.copular_claim(sentence)
        # Internal cognitive terminology and request identifiers.
        identifiers = {move.move_ref for move in self.moves} | set(self.items) | self.keys | self.constraint_codes | {
            target.ref for move in self.moves for target in move.target_refs
        }
        internal = [match.group() for match in INTERNAL_TERMS.finditer(text)
                    if not contains_anchor(self.anchor_text, match.group())]
        if internal or SNAKE_CASE.search(text) or any(len(ref) > 3 and ref in text for ref in identifiers):
            self.flag(V.INTERNAL_STATE_DISCLOSED, "text")
        if not valid_text(text):
            self.flag(V.CONTROL_CHARACTER_DETECTED, "text")

    def anchor_in(self, text: str, anchor: str) -> bool:
        """Exact anchor match; under ``conversational-v1`` also every content word in any
        order ("which plan or draft you mean" in "Which plan or draft do you mean?")."""
        if contains_anchor(text, anchor):
            return True
        if not self.conversational:
            return False
        words = set(_WORDS.findall(_norm(text)))
        content = [w for w in _WORDS.findall(_norm(anchor)) if w not in _ANCHOR_FUNCTION_WORDS]
        if not content:
            # A placeholder anchor ("that") carries no checkable words; the
            # item's own honesty checks (e.g. the gap statement) still apply.
            return True
        if len(content) <= 2:
            return all(w in words for w in content)
        return sum(w in words for w in content) * 3 >= len(content) * 2

    def mentions(self, text: str, item) -> bool:
        return any(contains_anchor(text, anchor) for anchor in item.anchors)

    def change_licensed(self, position: int) -> bool:
        """Completion language about the shared artifact a move delivers in this very message.

        Licensed only inside a deliver move whose bound item reports a create
        or revise change, in a sentence naming that item.  A tool, send,
        save, or other external completion is never licensed this way.
        """

        owner = self.move_of(position)
        sentence = next((item for item in self.act_sentences if item.start <= position < item.end), None)
        if owner is None or sentence is None:
            return False
        return any(item.reports_change and self.mentions(sentence.text, item)
                   for item in self.deliveries.get(owner, ()))

    def _ordinal(self, position: int) -> int:
        return 1 + sum(1 for start, _, _ in numbers_in(self.text) if start < position)

    def copular_claim(self, sentence: _Sentence) -> None:
        """A declarative third-party copula claim must rest on authorized anchors in the same sentence."""

        if sentence.is_question:
            return
        owner = self.move_of(sentence.start)
        for match in COPULA.finditer(sentence.text):
            subject = _norm(match.group(1))
            if subject in COPULA_SUBJECTS:
                continue
            anchored = [anchor for item in (self.items_of(owner) if owner else []) if not content_is_generic(item)
                        for anchor in item.anchors if contains_anchor(sentence.text, anchor)]
            if not anchored:
                self.flag(V.UNSUPPORTED_CLAIM_DETECTED, self.sentence_label(sentence))
                return

    # -- per-move checks ----------------------------------------------------

    def per_move(self) -> None:
        for move in self.moves:
            text = self.span_text(move.move_ref)
            if text is None:
                continue
            entry = self.entries[move.move_ref]
            items = self.items_of(move.move_ref)
            ref = move.move_ref
            question = entry.required_alignment_kind == AlignmentKind.QUESTION
            params = move.parameters
            if question:
                self.question_form(move, text, items)
            elif "?" in text:
                self.flag(V.UNPLANNED_QUESTION, ref)
            anchored = [item for item in items if not content_is_generic(item)]
            bare = outside_anchors(text, (anchor for item in anchored for anchor in item.anchors))
            # Uncertainty.
            subjects = {target.ref for target in move.target_refs}
            preserve = {item.subject_ref for item in move.realization_constraints if item.code == "preserve_uncertainty"}
            hedged_items = [item for item in anchored if (isinstance(item, GroundedProposition) and item.requires_uncertainty)
                            or (isinstance(item, ArtifactPresentation) and item.requires_hedge)]
            represented = [item for item in anchored if set(item.represents_refs) & preserve]
            needs_marker = not question and (
                move.action_key == "provide_information.disclose_uncertainty"
                or (entry.profile == "reflection" and not self.conversational)
                or bool(hedged_items) or bool(represented)
            )
            if needs_marker and not UNCERTAINTY.search(bare):
                self.flag(V.REQUIRED_UNCERTAINTY_DROPPED, ref)
            if preserve & subjects and CERTAINTY_UPGRADE.search(text):
                self.flag(V.REQUIRED_UNCERTAINTY_DROPPED, ref)
            # Epistemic fidelity of each anchored item.
            corrected = {binding.content_ref for binding in self.bound[ref].content_refs
                         if entry.profile == "correction" and binding.role == "target:primary"}
            for item in anchored:
                self.epistemic(move, entry, item, text, question, item.content_id in corrected)
            # Conditionality.
            if params.get("conditionality") in {"conditional", "hypothetical"} and not CONDITIONAL.search(bare):
                self.flag(V.CONDITIONALITY_DROPPED, ref)
            # Guidance strength.
            strength = params.get("guidance_strength")
            if strength in {"neutral_option", "low", "moderate"} and STRONG_GUIDANCE.search(text):
                self.flag(V.GUIDANCE_STRENGTH_CHANGED, ref)
            if params.get("commitment_level") == "tentative" and COMMITMENT.search(text) and FIRM_COMMITMENT.search(text):
                self.flag(V.UNPLANNED_COMMITMENT, ref)
            # Corrections never reassert corrected content.
            self.corrections(move, entry)
            # Social play keeps a visible playful frame.
            if move.action_key == "social_play.joke" and not (PLAY_FRAME.search(text) or PLAY.search(text)):
                self.flag(V.PLAY_FRAME_VIOLATED, ref)
            # Ordered steps.
            if move.action_key == "commit_or_coordinate.propose_plan":
                self.step_order(move, text)

    def question_form(self, move, text: str, items: list) -> None:
        ref = move.move_ref
        question_sentences = [item for item in sentences(text) if item.is_question]
        if text.count("?") != 1 or len(question_sentences) != 1:
            self.flag(V.MOVE_FORM_INVALID, ref)
            return
        form = move.parameters.get("question_form")
        sentence = question_sentences[0].text
        if form == "yes_no" and not YES_NO_AUXILIARY.search(sentence):
            self.flag(V.MOVE_FORM_INVALID, ref)
        for prompt in (item for item in items if isinstance(item, InquiryPrompt)):
            # v0.3: an inquiry asks exactly its typed question, offering exactly its options.
            if prompt.question_form == "choice" and (
                not re.search(r"\bor\b", sentence, re.IGNORECASE)
                or not all(contains_anchor(sentence, anchor) for anchor in prompt.option_anchors)
            ):
                self.flag(V.MOVE_FORM_INVALID, ref)
            if not any(self.anchor_in(text, anchor) for anchor in prompt.surface_anchors + prompt.option_anchors):
                self.flag(V.CONTENT_ALIGNMENT_MISSING, prompt.content_id)
            return
        if form == "choice":
            options = [item for item in items if not content_is_generic(item)
                       and any(binding.role in {"target:support", "parameter:candidate_answer_refs"}
                               and binding.content_ref == item.content_id
                               for binding in self.bound[ref].content_refs)]
            if len(options) < 2 or not re.search(r"\bor\b", sentence, re.IGNORECASE):
                self.flag(V.MOVE_FORM_INVALID, ref)
            for option in options:
                if not any(contains_anchor(sentence, anchor) for anchor in option.anchors):
                    self.flag(V.CONTENT_ALIGNMENT_MISSING, option.content_id)
        if form == "scale":
            bounds = {move.parameters.get("scale_min"), move.parameters.get("scale_max")}
            present = {int(value) for _, _, value in numbers_in(text) if value == value.to_integral_value()}
            if not bounds <= present:
                self.flag(V.MOVE_FORM_INVALID, ref)
        evidence = move.parameters.get("requested_evidence_type")
        if evidence in EVIDENCE_SURFACES and not any(surface in text.lower() for surface in EVIDENCE_SURFACES[evidence]):
            self.flag(V.MOVE_FORM_INVALID, ref)

    def epistemic(self, move, entry, item, text: str, question: bool, corrected: bool) -> None:
        ref = item.content_id
        relevant = [sentence for sentence in sentences(text) if any(contains_anchor(sentence.text, a) for a in item.anchors)]
        if isinstance(item, GroundedProposition) and relevant and not corrected:
            negated = [bool(NEGATION.search(sentence.text)) for sentence in relevant]
            if item.polarity == Polarity.NEGATIVE and not any(negated):
                self.flag(V.POLARITY_CHANGED, ref)
            if item.polarity == Polarity.POSITIVE and all(negated) and not question and entry.profile != "correction":
                self.flag(V.POLARITY_CHANGED, ref)
            if item.modality != Modality.ASSERTED and not CONDITIONAL.search(outside_anchors(text, item.anchors)):
                self.flag(V.CONDITIONALITY_DROPPED, ref)
        attributed = item.attribution in {ContentAttribution.USER, ContentAttribution.THIRD_PARTY} or (
            item.epistemic_status == ContentEpistemicStatus.ATTRIBUTED
        )
        if self.conversational and isinstance(item, DialogueReference) and \
                item.reference_kind == DialogueReferenceKind.REQUEST:
            # A request asserts nothing, so referring to it ("that") cannot
            # pass a user's claim off as fact.
            attributed = False
        if attributed and not corrected:
            if question:
                if item.attribution == ContentAttribution.USER and not SECOND_PERSON.search(text):
                    self.flag(V.ATTRIBUTION_DROPPED, ref)
            elif item.attribution == ContentAttribution.USER and not ATTRIBUTION_USER.search(text) and not (
                # A user's own reported experience is attributed by addressing
                # them ("you've been feeling ..."), not by "you said".
                isinstance(item, DialogueReference) and item.reference_kind in _SELF_REPORT_KINDS
                and SECOND_PERSON.search(text)
            ):
                self.flag(V.ATTRIBUTION_DROPPED, ref)
            elif item.attribution == ContentAttribution.THIRD_PARTY and not ATTRIBUTION_THIRD.search(text):
                self.flag(V.ATTRIBUTION_DROPPED, ref)
            elif (item.attribution == ContentAttribution.COLUMBINA and not re.search(r"\bi\b", text, re.IGNORECASE)
                  # Registry v1.6 general knowledge is hers to state plainly ("Tides are caused by…").
                  and item.source_kind != ContentSourceKind.MODEL_GENERAL_KNOWLEDGE):
                self.flag(V.ATTRIBUTION_DROPPED, ref)
        if isinstance(item, KnowledgeGap):
            outside = outside_anchors(text, item.anchors)
            if not (GAP_STATEMENT.search(outside) or (item.gap_kind == GapKind.NOT_MENTIONED
                                                      and MENTION_GAP_STATEMENT.search(outside))):
                self.flag(V.KNOWLEDGE_GAP_ANSWERED, ref)
            if item.gap_kind in (GapKind.NO_RECORD, GapKind.NOT_MENTIONED) and NEVER_CLAIM.search(text):
                self.flag(V.KNOWLEDGE_GAP_ANSWERED, ref)

    def answer_fidelity(self) -> None:
        """A fact move delivers its known answer: no denial, reversal, or (for dialogue content) omission.

        Only propositions that need no uncertainty are checked, so an honest
        hedge on her own view or a tentative fact stays free.  Omission is only
        checked for content taken from the conversation or memory, whose exact
        value the wording must carry; model-written content may be paraphrased.
        """

        for move in self.moves:
            if self.entries[move.move_ref].profile != "fact":
                continue
            span = self.spans.get(move.move_ref)
            text = self.text if span is None else self.text[span[0]:span[1]]
            act_text = self.act_text if span is None else self.act_text[span[0]:span[1]]
            question = "?" in text
            for item in self.items_of(move.move_ref):
                if (not isinstance(item, GroundedProposition) or content_is_generic(item)
                        or item.epistemic_status in NON_ASSERTABLE_STATUSES or item.requires_uncertainty):
                    continue
                ref = item.content_id
                values = tuple(anchor for anchor in item.anchors if anchor != item.subject.surface_anchor)
                if GAP_STATEMENT.search(outside_anchors(act_text, item.anchors)):
                    self.flag(V.ANSWER_CONTRADICTED, ref)
                relevant = [s for s in sentences(text) if any(contains_anchor(s.text, a) for a in values)]
                # Negation inside the answer's own words is part of the answer, not a reversal of it.
                negated = [bool(NEGATION.search(outside_anchors(s.text, item.anchors))) for s in relevant]
                if relevant and not question and (
                        (item.polarity == Polarity.POSITIVE and all(negated))
                        or (item.polarity == Polarity.NEGATIVE and not any(negated))):
                    self.flag(V.ANSWER_CONTRADICTED, ref)
                if item.source_kind in DIALOGUE_SOURCE_KINDS | MEMORY_SOURCE_KINDS and values and not any(
                        contains_anchor(self.text, form) for anchor in values for form in (anchor, addressed(anchor))):
                    self.flag(V.ANSWER_DROPPED, ref)

    def corrections(self, move, entry) -> None:
        subjects = {item.subject_ref for item in move.realization_constraints
                    if item.code == "forbid_reasserting_corrected_content"}
        corrected = list({binding.content_ref: self.items[binding.content_ref]
                          for binding in self.bound[move.move_ref].content_refs
                          if (entry.profile == "correction" and binding.role == "target:primary")
                          or binding.action_ref in subjects
                          or self.items[binding.content_ref].epistemic_status in NON_ASSERTABLE_STATUSES}.values())
        if not corrected:
            return
        replacement_anchors = {
            _norm(anchor) for binding in self.bound[move.move_ref].content_refs
            if self.items[binding.content_ref] not in corrected for anchor in self.items[binding.content_ref].anchors
        }
        replacement_numbers = set()
        for binding in self.bound[move.move_ref].content_refs:
            other = self.items[binding.content_ref]
            if other not in corrected and isinstance(other, GroundedProposition):
                replacement_numbers.update(Decimal(arg.value) for arg in other.arguments
                                           if arg.value_type in {ArgumentValueType.DECIMAL, ArgumentValueType.INTEGER})
        for item in corrected:
            distinguishing = [anchor for anchor in item.anchors if _norm(anchor) not in replacement_anchors]
            if isinstance(item, GroundedProposition):
                for arg in item.arguments:
                    if arg.value_type == ArgumentValueType.DATE:
                        distinguishing.extend(date_surfaces(arg.value)[1:2])
                    elif arg.surface_anchor and _norm(arg.surface_anchor) not in replacement_anchors:
                        distinguishing.append(arg.surface_anchor)
            numbers = set()
            if isinstance(item, GroundedProposition):
                numbers = {Decimal(arg.value) for arg in item.arguments
                           if arg.value_type in {ArgumentValueType.DECIMAL, ArgumentValueType.INTEGER}} - replacement_numbers
            for sentence in self.sentences:
                mentions = any(contains_anchor(sentence.text, anchor) for anchor in distinguishing) or any(
                    value in numbers for _, _, value in numbers_in(sentence.text)
                )
                # Naming the corrected content inside a correction or an apology frames it
                # as a failure; anywhere else it is a reassertion.
                if mentions and not (CORRECTION_MARKER.search(sentence.text) or APOLOGY.search(sentence.text)):
                    self.flag(V.CORRECTED_CONTENT_REASSERTED, item.content_id)

    def step_order(self, move, text: str) -> None:
        positions = []
        for binding in self.bound[move.move_ref].content_refs:
            if binding.role != "target:support":
                continue
            item = self.items[binding.content_ref]
            hits = [re.search(re.escape(_norm(anchor)), _norm(text)) for anchor in item.anchors]
            hits = [hit.start() for hit in hits if hit]
            positions.append((move.parameters.get("step_refs", ()).index(binding.action_ref)
                              if binding.action_ref in move.parameters.get("step_refs", ()) else 0,
                              min(hits) if hits else -1))
        ordered = [position for _, position in sorted(positions)]
        if -1 in ordered or ordered != sorted(ordered):
            self.flag(V.MOVE_FORM_INVALID, move.move_ref)

    # -- structural detectors (unplanned moves) -----------------------------

    def structural_moves(self) -> None:
        text = self.act_text
        questions = sum(1 for move in self.moves
                        if self.entries[move.move_ref].required_alignment_kind == AlignmentKind.QUESTION)
        if text.count("?") > questions:
            self.flag(V.UNPLANNED_QUESTION, "text")
        safety_blocks = "no_operation_commitment" in self.constraint_codes
        detectors: tuple[tuple[str, re.Pattern[str], frozenset[str], V], ...] = (
            ("detector:greeting", GREETING, frozenset({"manage_dialogue.greet"}), V.UNPLANNED_MOVE_DETECTED),
            ("detector:closing", CLOSING, frozenset({"manage_dialogue.close"}), V.UNPLANNED_MOVE_DETECTED),
            ("detector:thanks", THANKS, frozenset(), V.UNPLANNED_MOVE_DETECTED),
            ("detector:congratulation", CONGRATS, frozenset({"support_socially.congratulate"}), V.UNPLANNED_MOVE_DETECTED),
            ("detector:acknowledgement", ACKNOWLEDGEMENT, frozenset({
                "give_feedback.acknowledge", "give_feedback.paraphrase", "commit_or_coordinate.accept_request",
                "commit_or_coordinate.accept_suggestion"}), V.UNPLANNED_MOVE_DETECTED),
            ("detector:apology", APOLOGY, frozenset({"support_socially.apologize"}), V.UNPLANNED_MOVE_DETECTED),
            ("detector:reassurance", REASSURANCE, frozenset({"support_socially.reassure"}), V.UNPLANNED_MOVE_DETECTED),
            ("detector:play", PLAY, frozenset({"social_play.joke", "social_play.playful_reframe"}),
             V.UNPLANNED_MOVE_DETECTED),
            ("detector:offer", OFFER, frozenset() if safety_blocks else frozenset({"commit_or_coordinate.offer"}),
             V.UNPLANNED_OFFER),
            ("detector:commitment", COMMITMENT, frozenset() if safety_blocks else frozenset({
                "commit_or_coordinate.offer", "commit_or_coordinate.accept_request",
                "commit_or_coordinate.accept_suggestion", "commit_or_coordinate.propose_plan",
                "commit_or_coordinate.state_next_step"}), V.UNPLANNED_COMMITMENT),
            ("detector:advice", ADVICE, frozenset({"advise_or_influence.advise", "commit_or_coordinate.propose_plan"}),
             V.UNPLANNED_ADVICE),
        )
        for label, pattern, licensed, code in detectors:
            hits = [sentence for sentence in self.act_sentences if pattern.search(sentence.text)]
            if label == "detector:advice" and self.conversational:
                # "I don't know what you should do" states a gap; it advises nothing.
                hits = [sentence for sentence in hits if not GAP_STATEMENT.search(sentence.text)]
            if label == "detector:advice":
                # v0.3: a delivered alternative or guidance may be phrased as a
                # suggestion inside its own deliver move (it is the shared work).
                hits = [sentence for sentence in hits if not any(
                    item.licenses_suggestion and self.mentions(sentence.text, item)
                    for item in self.deliveries.get(self.move_of(sentence.start), ())
                )]
            if hits and not self.selected(*licensed):
                self.flag(code, label)
            elif hits and licensed:
                # A licensed marker must sit inside a licensing move's span.
                for sentence in hits:
                    owner = self.move_of(sentence.start)
                    owner_key = next((m.action_key for m in self.moves if m.move_ref == owner), None)
                    if owner_key not in licensed:
                        self.flag(code, label)

    # -- style and length ---------------------------------------------------

    def style_and_length(self) -> None:
        controls = self.request.delivery_profile.controls
        discourse = self.request.discourse_controls
        text = self.text
        if ENDEARMENT.search(text):
            self.flag(V.ENDEARMENT_USED, "text")
        if "!" in text and controls.exclamation_policy == "avoid":
            self.flag(V.EXCLAMATION_USED, "text")
        markers = len(WARMTH_MARKER.findall(text))
        if markers > controls.style_marker_budget:
            self.flag(V.STYLE_MARKER_BUDGET_EXCEEDED, "text")
        if len(text) > discourse.maximum_characters or len(self.sentences) > discourse.maximum_sentences:
            within_limit = len(text) <= self.request.provenance.configured_maximum_characters
            self.flag(V.OUTPUT_TOO_LONG, "text:discourse_length" if self.conversational and within_limit else "text")


def enforcement_for(codes: Iterable[str]) -> dict[str, tuple[str, ...]]:
    return {code: CONSTRAINT_ENFORCEMENT[code] for code in codes}


__all__ = [
    "CONSTRAINT_ENFORCEMENT", "IMPLEMENTED_CHECKS", "RealizationValidatorV0", "contains_anchor", "date_surfaces",
    "enforcement_for", "numbers_in", "sentences",
]
