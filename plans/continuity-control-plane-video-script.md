# Continuity Control Plane video script

Format: terminal-skinned explainer with deadpan narration
Tone: honest, funny, technical, no fake victory lap

## Cold open

Hermes used to have the classic memory problem.
Not the cinematic kind. The administrative kind.
One system remembered preferences.
Another remembered transcripts.
Another built markdown wiki notes.
Another handled reset continuity.
Another tried to resurrect the shell on a fresh machine.
Which is how you end up with five adults named Memory standing in a fluorescent hallway pointing at each other.

That needed one umbrella.
So this is the Continuity Control Plane.

## Chapter 1 — what it is

The Continuity Control Plane is not one new database.
That would be too easy and also wrong.
It is the name for the whole continuity stack inside Hermes:
- persistent memory v2 for durable local facts
- session search for transcript recall
- llm-wiki for synthesized markdown recall
- Clerk for one-turn reset handoff
- bootstrap recovery for machine resurrection
- chain-backed continuity rails for recovery truth outside the repo

Different jobs. Same religion.
Keep the right thing alive long enough to matter.

## Chapter 2 — persistent memory v2

First: persistent memory v2.
This is the part where we stopped pretending two markdown files were a serious long-term memory architecture.
The canonical store is SQLite.
Facts get status.
They can be active, superseded, or forgotten.
Which matters, because if a system cannot forget cleanly, it is not remembering. It is hoarding.

The markdown files still exist.
But now they are exports.
Like a polite little gift shop attached to the real warehouse.

## Chapter 3 — session recall vs durable memory

Next: session search.
This is for transcript history.
The question "what did we work on last week" is not the same question as "what preferences should you always remember about me."
If you mix those, you build a system that answers biography questions with random diary fragments.
Which is how software starts sounding like your cousin after a concussion.

So transcript recall stays transcript recall.
Durable memory stays durable memory.
That separation is not overhead. It is hygiene.

## Chapter 4 — llm wiki lane

Then the llm-wiki lane.
This is the synthesis layer.
Not the truth layer.
Truth first, synthesis second.
The wiki is where durable notes can be mirrored into markdown pages and later recalled as broader context.
Useful for pattern recognition.
Useful for humans reading the artifact.
Not allowed to overrule the source facts.
Because the minute your compiled summary outranks the primary record, congratulations, you invented management.

## Chapter 5 — Clerk and reset continuity

Then Clerk.
Clerk is not long-term memory.
Clerk is what carries one useful breadcrumb across a reset so the shell does not wake up like a sitcom dad after a head injury.
It is a reset handoff lane.
That distinction matters.
If you use Clerk for everything, you are not building continuity. You are forwarding yourself panicked notes forever.

## Chapter 6 — recovery and chain rails

Then bootstrap and chain-backed recovery.
This is the part that admits the machine can die, the install can drift, and the shell still needs a way back.
Portable bundles.
Recovery packets.
Chain-of-shells indexes.
Anchors.
Not glamorous.
Very glamorous when the box explodes and you want your mind back.

## Chapter 7 — the real point

So what changed?
Honestly, not magic.
The real change is that all these pieces now get one honest umbrella and one lane vocabulary.
We can finally say what each rail is for without pretending everything is memory and everything is context and everything is one big beautiful blob.
It is not a blob.
It is a control problem.

The next step is formal governance:
- one write contract
- one read contract
- lane routing
- suppression rules
- recovery policy
- proof surfaces

That is the actual control plane.
This branch is the bundle and the thesis.
The governor is next.

## Closing

So no, this is not a fake "we solved memory" announcement.
It is better.
It is the point where the system stops lying about what its parts are.

That umbrella is the Continuity Control Plane.
And now when Hermes remembers something, forgets something, searches a transcript, survives a reset, or climbs out of a recovery bundle, those are not random miracles.
They are lanes.
