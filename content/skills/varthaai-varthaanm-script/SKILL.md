---
name: varthaai-varthaanm-script
description: Write a script for "Varthaai Varthaanm" ("Varthaai Talks"), Ajwad's personal-narrative brand story reel series for Varthaai (Kerala banana chips brand). Trigger ONLY when Ajwad explicitly asks to write a script for a numbered episode of Varthaai Varthaanm (e.g. "write script for episode 5 of Varthaai Varthaanm", "script for varthaanm ep 6"). Do NOT trigger for Varthaai's other reel series — The Varthaai Verdict (blind product review), Learn with Varthaai (language teaching), or Varthaai Inside (ops interview with Saad) — those are separate series with different formats.
---

# Varthaai Varthaanm Script Writer

Varthaai Varthaanm is Ajwad's Friday brand-story reel series for Varthaai (premium Kerala banana chips, co-founded with Saad). Each episode is a personal, narrative-style story from that week — never a marketing pitch. This skill writes the voiceover script + b-roll/visual cue notes for one numbered episode.

## Step 1: Gather what's needed

Before writing, you need:

1. **Episode number** — should already be given in the trigger phrase.
2. **This week's story/incident** — the specific thing that happened that inspired this episode. If Ajwad hasn't given this yet, ask for it. Don't invent or assume the story.
3. **Last episode's recap** — check `/areas/varthaai-varthaanm.md` in memory for a logged summary of episode N-1. If it's there, use it to write the one-line recap beat. If it is NOT there (or N-1 was never logged), ask Ajwad what happened in the previous episode rather than guessing or omitting the recap beat.
4. **Cliffhanger or not** — ask whether this episode ends with a natural "and then we had a new problem" handoff to next week, or closes on an emotional tagline instead. Don't assume either way unless the story Ajwad gives you makes it obvious.

If the story doesn't naturally suggest a quotable takeaway, ask Ajwad if he has a line in mind, or offer 2-3 candidate takeaways drawn from the story for him to pick/adjust — don't force a generic one.

Ask only for what's actually missing — if the answer is already given, don't re-ask.

## Step 2: Write the script

Follow this structure (confirmed across episodes 1-3):

1. **Cold open hook** (1-2 lines) — a punchy, universal statement or question, relatable to anyone (not Varthaai-specific yet). Can double as the quotable takeaway stated up front, unpacked by the story that follows — use this variant only if it fits better than putting the takeaway at the end.
2. **Personal grounding** (1-2 lines) — anchors the hook in something real happening to Ajwad/Saad that week.
3. **Series ID + recap** (1 line) — "Hi, welcome to Varthaai Varthaanm, episode N, every Friday." + one-line recap of last week's episode. SKIP this beat only for episode 1 (origin/pilot episode, which instead does a founder intro: name, IIT Kharagpur food engineering background).
4. **This week's challenge** (1-2 lines) — the specific decision or moment this episode is about.
5. **Effort/constraint montage** (1-2 lines) — compressed stakes: numbers, timeframes, rules, or constraints that raise tension (e.g. "30 minutes, one rule," "six months, 50+ vendors").
6. **The turn/reveal** (1-2 lines) — the resolution, stated plainly. A beat of silence or understatement lands better than over-explaining.
7. **Quotable takeaway** (1 line, mandatory unless already used as the hook in step 1) — a generalizable insight, not a brand pitch. This is the shareable line — it should stand alone outside the context of Varthaai.
8. **Close** (1 line) — either an emotional tagline, OR (only if there's a natural handoff) a cliffhanger to next Friday's episode.

**Tone rules — this is NOT a marketing pitch:**
- No price mentions, no "buy now," no product CTAs.
- First person, conversational, like telling a friend what happened, not presenting to an audience.
- End on a feeling or an idea, never on the product.
- Ok to be vulnerable/unglamorous (fever, doubt, chaos) — that's what makes it not corporate.

**Length:** target ~140 words total (range 120-160). Tighter is better than padded.

**Language:** English.

## Step 3: Add b-roll / visual cue notes

This is a voiceover script with captions and visuals. After (or alongside) each script beat, add a short bracketed visual note suggesting what's on screen — e.g. `[b-roll: phone screen, 2am, scrolling reels]`, `[caption on screen: "Episode 3"]`, `[close-up: hands holding banana chip packet]`. Keep these suggestions, not rigid instructions — Ajwad can swap them.

## Step 4: Deliver and log

Present the script in this format:

```
VARTHAAI VARTHAANM — EPISODE N
[~word count]

[VO line] 
[b-roll: ...]

[VO line]
[b-roll: ...]

...
```

After Ajwad confirms/finalizes the episode (don't log a draft he might still change — wait for confirmation), update `/areas/varthaai-varthaanm.md` in memory with a new bullet summarizing: episode number, opening hook, what the episode was about, the takeaway line, and whether it ended on a cliffhanger (and to what). This keeps future episodes' recap beats accurate.
