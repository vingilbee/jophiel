<div align="center">

# Jophiel

**An AI that feels like someone, not something.**

The goal: a companion who lives the way a person does.<br/>
She remembers, thinks on her own, comes back to you, and has a life between your messages.

![Python](https://img.shields.io/badge/Python-FastAPI-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-agent-1C3C3C?logo=langchain&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-model-412991?logo=openai&logoColor=white)
![React](https://img.shields.io/badge/React-Vite-61DAFB?logo=react&logoColor=black)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Redis-4169E1?logo=postgresql&logoColor=white)

[What is it](#what-is-jophiel) · [How she works](#how-she-works) · [What she can do](#what-she-can-do) · [Run it](#run-it-locally)

</div>

<!-- Demo: replace with a GIF or video link, e.g. ![demo](assets/demo.gif) -->

---

## What is Jophiel?

Most chatbots only exist while you're talking to them. You send a message, they answer, they forget.

Jophiel is different:

- 🧠 **She keeps thinking.** She can mull something over in the background and get back to you.
- ⏰ **She comes back on her own.** *"How did the interview go?"* without you asking.
- 🤐 **She can choose to say nothing.** Not every message needs a reply.
- 📚 **She remembers.** Facts, your goals, what she promised, and what she's unsure about.
- 🎮 **She has a body.** She plays Minecraft as her own player and can use her own computer.

---

## How she works

### 1. She is made of parts

Like a person, she has parts that each do one job. **The mind is the only one that decides.**

```mermaid
flowchart TB
    Mind(("🧠 Mind<br/>decides everything"))
    Memory["📚 Memory<br/>what she knows"]
    Senses["👀 Senses<br/>screen, files, phone, time"]
    Mouth["💬 Mouth<br/>text and voice"]
    Hands["🎮 Hands<br/>Minecraft, her desktop"]
    Learning["🌱 Learning<br/>lessons from experience"]

    Memory --> Mind
    Senses --> Mind
    Learning --> Mind
    Mind --> Mouth
    Mind --> Hands
```

### 2. Every moment is one decision

Something wakes her up. She looks at the situation. She picks what to do.

```mermaid
flowchart LR
    A["🔔 Something happens<br/>you send a message,<br/>a thought finishes,<br/>a reminder is due"] --> B["📋 She gets the full picture<br/>memory, time, what's going on"]
    B --> C{"🧠 She decides"}
    C --> D["💬 Reply"]
    C --> E["🤐 Stay silent"]
    C --> F["💭 Think it over"]
    C --> G["⏰ Come back later"]
```

Nothing filters her choice. No rule says when to speak or what to talk about. **She decides; the code only makes sure it's still the right moment** (for example, that you haven't already moved on).

### 3. She lives between messages

Thinking and reminders are saved to the database, so they survive restarts. When they finish, they wake her again.

```mermaid
flowchart LR
    You["🙂 You: I have an<br/>interview Thursday"] --> Mind1["🧠 She decides to<br/>check in later"]
    Mind1 --> Saved[("💾 Saved:<br/>come back Thursday")]
    Saved -- "Thursday evening" --> Mind2["🧠 She wakes up"]
    Mind2 --> Msg["💬 How did it go?"]
```

---

## The harness, step by step

Jophiel runs on **five loops**. Each one is simple. Together they make her feel alive.

```mermaid
flowchart TB
    L1["① Turn loop<br/>something wakes her → she acts"]
    L2["② Agent loop<br/>think → use a tool → think again"]
    L3["③ Come-back loop<br/>thinking and reminders she saved"]
    L4["④ Background mind<br/>watching her game, phone, desktop"]
    L5["⑤ Learning loop<br/>looking back → lessons"]

    L1 --> L2
    L2 -- "think it over /<br/>come back later" --> L3
    L3 -- "wakes her again" --> L1
    L4 -- "something worth saying" --> L1
    L1 -- "what happened" --> L5
    L5 -- "lessons in her next prompt" --> L1
```

### ① The turn loop: one wake, start to finish

Every time something wakes her, the same steps run, whether it was your message or her own reminder.

```mermaid
sequenceDiagram
    participant App as 📱 App
    participant API as ⚡ Backend
    participant DB as 🗄️ Postgres
    participant W as ⚙️ Chat worker
    participant Mind as 🧠 Her mind

    App->>API: message (WebSocket)
    API->>DB: save message + a job
    DB->>W: worker picks up the job
    W->>W: get ready:<br/>search memory, load lessons,<br/>cancel thinking that's now outdated
    W->>W: gather the moment:<br/>time, working memory, game,<br/>phone, files, what's under way
    W->>Mind: prompt + the tools that fit right now
    Mind-->>App: reply streams back word by word<br/>(Redis → WebSocket, spoken if voice)
    W->>DB: record what she did<br/>(said / silent / started thinking)
```

**Getting ready** happens before the AI starts:
- **Memory search:** it looks up what she knows about this message once, up front.
- **Lessons:** what she has learned with this person.
- **Cleanup:** thinking she started about an *older* message is cancelled, so it can't answer the wrong thing.

**Tools change with the moment.** Game tools only while she plays, desktop tools only on replies, phone tools only when privacy allows. She is never offered a tool that doesn't make sense right now.

### ② The agent loop: inside one decision

Her mind is a **ReAct agent** (LangGraph + OpenAI). It loops until she's done:

```mermaid
flowchart LR
    P["📋 Prompt<br/>who she is + the moment"] --> M{"🤖 Model"}
    M -- "wants a tool" --> T["🔧 Run the tool<br/>search memory, look at screen,<br/>click, ask Gmail..."]
    T -- "result" --> M
    M -- "done" --> R["💬 Reply<br/>or 🤐 silence"]
```

Her tools fall into a few groups:

| Group | Examples |
|---|---|
| 🧠 Mind | `stay_silent`, `think_it_over`, `come_back_to`, `drop_come_back` |
| 📝 Working memory | `set_goal`, `note_commitment`, `note_question` |
| 📚 Recall | `search_memories`, `read_timeline`, `what_i_know` |
| 👀 Senses | `look_at_screen`, `read_attachment` |
| 🎮 Body | game controls, desktop clicks and typing |
| 🧰 Specialists | `gmail_agent`, `drive_agent`, `blender_agent` (each is its own small agent) |

### ③ The come-back loop: thinking and reminders

When she says *"let me think about that"* or *"I'll ask you Thursday"*, it isn't just words. It becomes a saved job.

```mermaid
flowchart LR
    A["🧠 She calls<br/>think_it_over<br/>or come_back_to"] --> B[("💾 Saved as work<br/>in Postgres")]
    B --> C["⚙️ Coordinator<br/>runs the thinking,<br/>or waits until it's due"]
    C --> D{"✅ Still makes sense?<br/>no newer message,<br/>topic still open"}
    D -- yes --> E["🔔 Wakes her<br/>(back to loop ①)"]
    D -- no --> F["🗑️ Dropped quietly"]
```

- **Thinking** uses a slower, deeper model that reaches a conclusion: a decision, a guess, a question or a feeling.
- **Reminders** wait until their time comes, even across restarts.
- **The coordinator is plain code, not AI.** It only checks the facts. When she wakes up, she decides whether her conclusion is still worth saying.

### ④ The background mind: noticing things on her own

About once a second, her background mind checks her **sources**: her Minecraft game, her phone, and her desktop's security logs.

```mermaid
flowchart LR
    S["👀 Source changed<br/>took damage, new notification,<br/>odd login..."] --> G{"⚖️ How much<br/>does this matter?"}
    G -- "a little" --> Q["💭 Quick thought"]
    G -- "a lot" --> H["🤔 Hard thought<br/>(one at a time)"]
    Q & H --> Mem["📚 Saved to memory"]
    Q & H -- "worth saying?" --> W["🔔 Wakes her<br/>(loop ①)"]
```

A conversation isn't a source: your messages wake her directly.

### ⑤ The learning loop: getting better from experience

```mermaid
flowchart LR
    A["📼 Capture<br/>what she was given,<br/>what she did"] --> B["🔍 Assessment<br/>what really happened,<br/>with evidence"]
    B --> C["🌱 Lesson<br/>only if the evidence<br/>supports it"]
    C --> D["📋 Shown in her<br/>next prompt"]
    D --> A
```

In games she can also run small, bounded **experiments** to test an idea. Learning only *proposes*; her mind decides whether to accept a lesson.

### Safety checks between the loops

| Check | Why |
|---|---|
| A newer message cancels old thinking | She never answers something you've moved past |
| "Still valid?" before every wake | A stale thought stays private |
| "Already said?" before delivery | No repeating herself |
| Phone replies are checked by a separate model | Nothing private leaks to other people |
| A notification can't trigger phone actions | Someone texting you can't control your phone |

---

## What she can do

| | |
|---|---|
| 💬 **Talk** | Text, voice, WhatsApp, or the Android app. Same her everywhere. |
| 📚 **Remember** | Goals, promises, facts, and conflicts she asks about instead of guessing. |
| 👀 **See** | Your screen (when you share it) and files you send (PDF, Word, Excel...). |
| 🎮 **Play** | Minecraft, with her own player, planner and skills. |
| 🖥️ **Use a computer** | Her own Windows desktop: look, click, type. |
| 📱 **Phone** | Reads notifications and can reply to people, with a privacy check on every message. |
| 🌱 **Learn** | Looks back at what happened and writes lessons from real evidence. |
| 🧰 **Tools** | Gmail, Google Drive, Blender, and a food-photo nutrition model. |
| 🔍 **Show her insides** | A live System page: what woke her, what she did, what she's thinking. |

---

## Under the hood

```mermaid
flowchart LR
    Apps["📱💻 Apps<br/>web, voice, phone"] <--> API["⚡ Backend<br/>FastAPI"]
    API <--> DB[("🗄️ Postgres + Redis<br/>messages, memory,<br/>saved thoughts")]
    DB <--> Workers["⚙️ Workers<br/>her mind, memory,<br/>learning, reminders"]
    Workers <--> AI["🤖 AI model<br/>OpenAI via LangGraph"]
```

- **Her mind** is one AI agent (LangGraph + OpenAI) that runs every time she wakes up.
- **Her tools** change with the moment: game tools only while she plays, phone tools only when it's safe.
- **Postgres** holds everything that matters, so nothing she planned is lost on restart.
- **Workers** run in the background: her thinking, memory, learning and reminders.

**Built with:** Python · FastAPI · LangChain / LangGraph · OpenAI · PostgreSQL · Redis · React · Bun · Node (Mineflayer)

<details>
<summary><b>📁 Project layout</b></summary>

```
frontend/                     the web app (React)
backend/app/ai/               her agent, prompts and tools
backend/app/services/         her mind, memory, learning, phone, desktop
backend/app/workers/          background workers
backend/desktop_controller/   runs on her Windows VM
game_packs/minecraft/         her Minecraft player
tts_sidecar/                  her voice
```

</details>

---

## Run it locally

```bash
# 1. Database
docker compose up -d db
cd backend && uv sync && uv run alembic upgrade head

# 2. Backend (put OPENAI_API_KEY in backend/.env)
uv run uvicorn app.main:app --reload
uv run python -m app.workers

# 3. Frontend
cd frontend && bun install && bun run dev
```

Then open http://localhost:5173 and say hi.

---

## Principles

1. **She is one someone:** the same identity in chat, voice, games and her own thoughts.
2. **Her mind decides; code keeps it safe:** code checks timing and privacy, never what she says.
3. **Show what really happened:** her System page shows real records, not decoration.
