"""Versioned scope mapping. No lexical intent inference or action permission."""


def embedding_for(scope):
    return {'current': 'direct', 'quoted': 'quoted', 'reported': 'reported', 'imagined': 'hypothetical'}[scope]


def predicate_description(predicate, fallback):
    return {
        'opinion_request': 'A present request for Columbina\'s judgment, preference or evaluation, including what she would choose in an imagined situation. The requested outcome is her view, not a produced deliverable. Hypothetical ownership of an object does not ask for her identity.',
        'identity_request': 'A request for an actual identity attribute of the assistant or user (their name or role). Not the name of an object they own, might own, or are asked to judge.',
        'collaborative_content_request': 'A request to produce or modify a deliverable (draft, list, plan, generated options, edited content, artifact comparison). A personal choice, hypothetical preference, or conversational explanation/comparison of her own position is not by itself a deliverable request. Choose by requested outcome, not verbs such as name, compare or explain.',
        'elaboration_request': 'A conversational request to explain, expand or compare an already discussed meaning, including her own position and how it relates to earlier statements. Do not convert an explanation of her position into artifact work or a literal quote-retrieval request.',
    }.get(predicate, fallback)
