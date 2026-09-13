# Signup Funnel Redesign — Planning Meeting

Date: 2026-09-13. Attendees: Maya (PM), Jordan (Eng), Priya (Design).

Maya (PM): Alright, let's dig into the signup funnel. It currently has 5 steps,
and that feels heavy. Last week we saw a 12% drop-off at step 3, the "team
setup" screen.

Jordan (Eng): Confirmed on my side too — the step-3 API latency hasn't changed;
it's around 400ms p95. So it's not a performance cliff.

Priya (Design): From the research calls, users said step 3 felt confusing —
they didn't know why we needed team names upfront. That came up in most
sessions.

Maya (PM): Interesting. Whoever gets that far is already bought in — they're
trying to sign up — so friction there is expensive. We have two ways to go:
(A) collapse the funnel from 5 steps to 3, or (B) keep 5 steps but add a
progress indicator so people know how far along they are.

Jordan (Eng): Rough sizing: A is maybe 2 weeks of work. B is three days — it's
mostly cosmetic.

Priya (Design): If we go with A, I can have the redesigned step screens ready
by Thursday.

Maya (PM): Good. I'm going to commit to A. My reasoning: if step 3 confusion is
what's driving the drop-off, reducing the funnel to 3 steps should lift signup
completion by 15% within two weeks of shipping.

Jordan (Eng): And if the numbers don't move?

Maya (PM): Then we revisit B and the team-name question. Let's review the data
on 2026-09-27 — that gives us the two-week window after launch.

Priya (Design): Works for me.

Maya (PM): Great — decision made, A it is. I'll set up the bet and the review
so we're forced to look at the data.
