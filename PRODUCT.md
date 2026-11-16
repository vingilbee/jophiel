# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **The owner** (the builder). Talks with Jophiel every day, plays Minecraft with her, and watches her insides: what she thinks, remembers and learns, and how her parts work together. Only the owner sees her insides (`OWNER_EMAILS`).
- **Other people, later.** They may sign up, but they only talk with her: chat, voice, integrations. They never see her insides.

It's a personal project for now.

## Product Purpose

Jophiel is a general conversational companion with a persistent identity, personality, memory and an always-on mind. People talk with her about anything: social chat, reflection, curiosity, disagreement, practical help. She doesn't default to tasks or deliverables. She also plays games alongside the user with her own body (Minecraft) and learns from what happens.

Success is a conversation that feels continuous and natural with a consistent someone, not a tool that answers.

## Positioning

She isn't a single model call. She's a set of parts that run all the time and hand work to each other: a mind that decides everything she does and keeps thinking on its own, a mouth that delivers what she says, hands that play, memory, observation, and learning. Much of her life happens without a user message. Showing that living system is part of what makes her her.

## Operating Context

- Web app on a Mac (local dev; no cloud deployment yet). React + Vite frontend, FastAPI backend, background workers, Postgres, Redis.
- Pages: voice (dashboard), chat, predict (food photo, a specialist tool), integrations; plus owner-only views of her insides.
- The owner often has a Minecraft session running next to the app.

## Capabilities and Constraints

- Her parts: **Mind** (every evaluation: what woke her, what she did, what she keeps open and will come back to; quick and hard thoughts, planner), **Mouth** (delivery, in text and voice), **Hands** (game skills), **Memory**, **Observation**, **Learning** (assessments, lessons, trials, game lessons, proposals).
- Her insides are for the owner only; signals and details shown are the owner's own activity.
- Some parts show reasoning (hard thoughts) and others only outputs (an evaluation's tools and reply, the mouth's delivery). Never present made-up reasoning.

## Brand Commitments

- Name: **Jophiel** (renamed from Columbina on 2026-11-03). Pronouns: she/her.
- No art, names or imagery from the Genshin Impact character the old name came from.

## Evidence on Hand

- Real data from her parts: thoughts, memories, assessments, lessons, trials, game outcomes, captured decisions. No public users, testimonials or metrics yet; don't invent them.

## Product Principles

1. She is one someone: one identity across chat, voice, game and her own thinking.
2. Show what really happened. Inner views reflect real records, never decoration posing as data.
3. Her life doesn't wait for the user: background thinking, memory and learning are first-class.
4. The owner's view can be a showpiece, but it stays truthful.

## Accessibility & Inclusion

Standard care: readable contrast, keyboard use, and a still alternative when the OS asks for reduced motion.
