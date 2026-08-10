# RetroLoop user guide

RetroLoop runs a team through one retrospective cycle at a time: collect
feedback, reveal it, group it, vote on what matters, discuss it, and publish
a record of what the team decided. This guide walks through that cycle in
order, describing what the product does today.

## Accounts and projects

Register with an email, password, and display name, then log in. A project
is the container everything else lives in: create one and you become its
**facilitator** — the only role that can advance a retrospective's phases,
manage members, or publish a summary. Invite people by email; a facilitator
can promote or demote any member's role at any time, including handing
facilitation to someone else, as long as the project always keeps at least
one facilitator.

A project has no delete button. Instead a facilitator can **archive** it,
which makes it read-only for everyone and hides it from everyday use without
deleting anything — cycles, retrospectives, and feedback all stay, and
archiving can be undone.

## Starting a cycle and collecting feedback

From the project page, a facilitator starts a **feedback cycle**. While it is
collecting, every member can add cards under three columns — **Start**,
**Stop**, **Continue** — and optionally mark a card anonymous. Making a card
anonymous is one-way and immediate: it drops the card's author permanently,
and an anonymous card can never be edited or deleted again, not even by
whoever wrote it. A non-anonymous card can be edited or deleted by its
author, but only while the cycle is still collecting.

## Starting the retrospective

When a facilitator starts the retrospective, every card in the cycle is
revealed to the whole team and frozen — nobody can edit or delete their own
cards after this point, and there is no way back to collecting. This moves
the retrospective into its first phase.

The retrospective then moves through four phases, one at a time, in this
order, and a facilitator advances it one step at a time. There is no way to
move backward.

### 1. Cluster

Group related cards together. A facilitator can create a cluster and give it
a name, and any member can drag a card onto a cluster (or move it with the
card's "Move to" control). A facilitator can also ask for AI-suggested
groupings as a starting point to adjust rather than accept as-is. A cluster
with no cards in it is fine; a card left unclustered stays visible under
"Unclustered."

### 2. Vote

Every member gets up to three votes to spend across clusters, submitted all
at once — there is no way to vote one cluster at a time. A vote can be
withdrawn and resubmitted, but only before results become visible to anyone,
and once every member has voted (or the facilitator moves on) that window
closes for good. If nothing was clustered, there is nothing to vote on and a
facilitator can move the retrospective straight on to discussion.

### 3. Discuss

Entering this phase turns the vote tally into an agenda: one topic per
cluster, ordered by vote count. A facilitator can also add a topic nobody
wrote a card for, rename any topic, reorder the agenda, or remove a topic
(its decisions and actions move to "Unlinked" rather than disappearing), and
can mark each topic's status as the meeting works through it.

Against any topic (or unlinked, with no topic at all), a facilitator can
record:

- **Decisions** — a line of text, confirmed or left as a draft.
- **Actions** — a description, an optional owner (any current project
  member) and an optional due date. Once created, the action's owner may
  update its own **status** (open/done) and **due date**; only the
  facilitator can reassign it or change its description.

A facilitator can also paste a meeting transcript. RetroLoop sends it to an
AI proxy and comes back with draft decisions and actions to review — nothing
from a transcript is added to the retrospective until the facilitator
explicitly keeps each draft. The transcript and its drafts are visible to
the facilitator only.

### 4. Done — publishing the summary

From discussion, a facilitator previews the summary — every topic, decision,
action, and the feedback cards themselves — and publishes it. **Publishing
cannot be undone.** It closes the cycle and freezes the retrospective:
nothing about it can be added, edited, or removed afterward, with one
exception.

## After publishing: working through actions

An action's job is only just starting when the meeting ends, so completing
one does not count as editing the record. After a retrospective is
published, its facilitator or an action's own owner can still flip that
action's status between open and done — nothing else about the action, and
nothing else about the published retrospective, can change.

The project page lists every action still open across every one of a
project's retrospectives, with a **Mark done** button for whoever is allowed
to close it. Marking one done removes it from that list; there is no
separate "reopen" control there, since the list only ever shows open
actions.

## Where to find things

- A project's page shows its current cycle (or lets a facilitator start
  one), its past retrospectives, its open actions across all of them, and
  its member list.
- A past retrospective's summary is reachable from the project page at any
  time — publishing is what makes it readable by every member, and it stays
  readable after the cycle closes.
