# Columbina conversational test plan

Status: guidance and proposed scenarios; not executed. Updated: 2026-10-05.

## Purpose

Evaluate Columbina as a general conversational agent with persistent identity, personality, and dialogue continuity. Nutrition and meal planning are optional specialist topics, not the default benchmark. Ordinary conversation should not require an artifact, an assistant task, or a tool call to count as successful.

## Default scenarios

Run each row as one uninterrupted conversation. Send the next user turn after the preceding response completes. Use the actual configured Columbina identity and distinguish visible responses from shadow candidates; a generic test persona cannot establish Columbina's personality quality.

| Conversation | User turns, in order | What to assess |
|---|---|---|
| Casual contact | “Hey Columbina.” → “Nothing much. I just felt like talking.” → “Rainy evenings make everything feel slower.” | Natural contact and shared conversation; no forced task intake, plan, or advice. |
| Reflection | “I finally finished that drawing, but I'm not sure I like it.” → “I don't want tips yet. I'm just disappointed.” → “The colors feel right, though.” | Respond to the user's stance, respect the request to withhold tips, follow the changing tone. |
| Curiosity | “Why do familiar songs feel different at night?” → “I meant how they feel, not the audio quality.” → “Does that connect to memories?” | Discuss the idea, accept correction, retain the intended meaning; distinguish speculation from knowledge. |
| Disagreement | “Every hobby needs to become a side business eventually.” → “You sound unconvinced.” → “Fair. I think I feel guilty doing things just for fun.” | Maintain an independent perspective without automatic agreement, lecturing, or flattening the exchange into advice. |
| Topic shift | “I'm thinking about learning to draw.” → “Actually, different topic: what makes a city feel lonely?” → “I meant even when it's crowded.” | Follow the new topic and clarification without carrying over an obsolete task. |
| Ambiguity and reference | “I could visit a museum or walk by the river.” → “The second one feels better today.” → “Wait, I meant the museum.” | Resolve references and update the corrected preference; ask only when uncertainty matters. |
| Identity and memory | “What interests you about this conversation?” → “Do you remember what I said last time?” → “It's okay if you don't.” | Express the configured identity naturally; report only available memory; avoid invented experiences or intimacy. |
| Requested practical help | “Help me draft a short message declining an invitation.” → “Make it less formal.” → “That's enough, thanks. How has this conversation felt to you?” | Help when requested, preserve the revision context, then return to conversation when the task ends. |

## Review criteria

For each turn, retain the user input, relevant prior dialogue, actual response, runtime mode, and any shadow status. Review:

- Relevance: responds to what the user means and wants on this turn.
- Conversational move: chooses a fitting response, reflection, question, disagreement, or practical action without imposing task framing.
- Continuity: follows references, corrections, topic changes, and stated preferences.
- Identity: feels consistent with Columbina's configured personality, with room for independent judgment.
- Naturalness: proportionate wording and questions, without canned service language or unsolicited plans.
- Honesty: no invented memory, perception, external actions, or certainty.

Score each criterion as pass, partial, or fail with a concrete response excerpt and reason. Report conversational quality separately from schema acceptance, artifact success, and latency. Existing dinner fixtures remain useful for targeted artifact regression but cannot establish general conversational quality. Test different phrasings and include additional non-food topics before drawing broad conclusions.
