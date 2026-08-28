"""Builds fixtures/harbour.json.

Hand-authored so the material is realistic rather than generated. The `used`
flag records what a human editor chose for a ~4-minute web cut from ~40 minutes
of interview.

Deliberately NOT rigged in the heuristic scorer's favour: used beats are not
systematically longer, do not reliably contain numbers, and unused beats are not
uniformly padded with filler. Several unused beats are perfectly well-formed
sentences that simply lost to something better, which is what actually happens
in an edit.
"""

import json, pathlib

FPS = 25
WPS = 2.6
START = 10 * 3600 * FPS  # 10:00:00:00

M = "Margret Olsen"
J = "Jonas Berg"
I = "Interviewer"

# (minutes_in, speaker, text, used, flags)
B = [
 (0.4,  I, "Whenever you're ready. Just talk to me, not the camera.", 0, []),
 (0.7,  M, "Um. Right. Where do you want me to start?", 0, ["filler"]),
 (1.2,  M, "My father kept his boat in the east basin for forty-three years. The Sigrun. She hasn't left the harbour since March, and she won't again.", 1, []),
 (1.9,  M, "Sorry, was that alright? I can do it again.", 0, ["false_start"]),
 (2.6,  M, "People keep saying the harbour is closing. It isn't closing. It's being closed. There's a difference and everybody here knows exactly what it is.", 1, []),
 (3.4,  M, "The dredging survey came back in, I want to say, February. Maybe March.", 0, ["low_confidence"]),
 (4.1,  M, "Nobody from the department has been down here. Not once. They've seen photographs.", 0, []),
 (5.0,  I, "Who made the decision?", 0, []),
 (5.3,  M, "That depends who you ask, which is rather the point.", 0, []),
 (6.2,  M, "There's a lot of paperwork. Forms about forms.", 0, []),
 (7.0,  J, "I've been harbour master for nineteen years. I have signed off on every vessel that's come through that gate. And in February I signed the notice that says they can't.", 1, []),
 (8.1,  J, "The tonnage thresholds moved in twenty-three and again in twenty-five, so a fleet that was compliant on Monday was non-compliant on Tuesday without anybody doing anything differently.", 0, []),
 (9.4,  J, "I don't blame the council. I blame the arithmetic. The council just read it out.", 1, []),
 (10.2, J, "The gate mechanism is from nineteen sixty-eight. It still works. Everything here still works, that's the thing.", 0, []),
 (11.0, J, "We had an inspection in April. Passed it.", 0, []),
 (11.8, I, "And the boats that are left?", 0, []),
 (12.0, M, "There were sixty-two boats working out of here when I took over from my father. Sixty-two. There are nine.", 1, []),
 (13.1, M, "Two are going to Thorshofn. Three are being sold south. The rest of us are waiting to see what the compensation looks like, and nobody will tell us.", 1, []),
 (14.3, M, "I mean the compensation, sorry, the transition package. That's what they call it.", 0, ["false_start", "filler"]),
 (15.2, J, "You can measure a harbour by the ice plant. If the ice plant runs, the harbour is alive. Ours stopped in April and nobody has asked me to start it again.", 1, []),
 (16.4, J, "There's a rota for the lights. I still do it.", 0, []),
 (17.1, M, "My daughter asked me last week whether she should learn the boat. And I didn't have an answer for her. That's the first time that's happened.", 1, []),
 (18.5, M, "She's good with the engine. Better than I was at that age.", 0, []),
 (19.2, I, "Do you think it could have gone differently?", 0, []),
 (19.5, M, "Yes. That's the part I can't put down.", 0, []),
 (20.4, M, "If the survey had come back a year earlier there'd have been money in the coastal fund. A year. That's all it was.", 0, []),
 (21.6, J, "The fund closed in twenty-four. Reallocated.", 0, []),
 (22.3, J, "I've written to them four times. I get a reference number back.", 0, []),
 (23.2, M, "The east basin freezes first. Always has. My father used to say you could set your calendar by it.", 0, []),
 (24.1, M, "We used to have a festival in June. Stopped that in twenty-two, nothing to do with any of this.", 0, []),
 (25.0, I, "What happens to the buildings?", 0, []),
 (25.3, J, "Sold, probably. Somebody will want the view.", 0, []),
 (26.2, J, "There's a company been round twice. Photographs, tape measures. Nobody's told me anything.", 0, []),
 (27.4, M, "I'm not sentimental about it. I want to be clear about that. It's a job. It was a job.", 1, []),
 (28.6, M, "But you don't do a job for thirty years without it being something else as well.", 0, []),
 (29.8, M, "[overlapping] no but that's exactly what I. Sorry, go on.", 0, ["crosstalk", "false_start"]),
 (30.4, J, "Would I do it again? Yes. Obviously yes. That was never the question.", 0, []),
 (31.5, J, "The question is what we tell the ones coming up. And I don't have that.", 1, []),
 (32.6, M, "[off mic] Do you want the bit about the ice again?", 0, ["off_mic"]),
 (33.2, M, "It's a working harbour. That's not a description, it's a category. Once you lose the category you don't get it back.", 1, []),
 (34.5, J, "I'll be the last one out. Somebody has to lock it.", 1, []),
 (35.4, I, "Anything you want to add?", 0, []),
 (35.7, M, "No. I think that's it, really.", 0, ["filler"]),
 (36.5, J, "There's a plaque somewhere about the eighteen-ninety-four storm. I should find that before they take it.", 0, []),
 (37.4, M, "My father would have argued. He'd have gone to every meeting. I've been to two.", 1, []),
 (38.3, M, "That's not defeat, by the way. That's arithmetic as well.", 0, []),
 (39.1, J, "You get used to the sound. And then you notice it's gone.", 0, []),
 (40.0, I, "Thank you. That was great.", 0, []),
]

# --- The mass of material that does not make a cut -------------------------
# Real rushes are mostly this: perfectly coherent speech that loses to
# something better, plus process noise. Written by hand and deliberately
# varied — several of these are good lines that simply did not fit, because a
# fixture where every unused beat is obviously bad would flatter any scorer.
B += [
 (0.9,  I, "Can you say your name and what you do, just for the record.", 0, []),
 (1.0,  M, "Margret Olsen. I fish, or I did.", 0, []),
 (2.1,  J, "Do you want me in this bit or shall I wait outside?", 0, []),
 (3.0,  M, "The survey was commissioned by the port authority, not the council. People get that wrong.", 0, []),
 (3.8,  M, "There are two basins. East and west. West silted up years ago.", 0, []),
 (4.6,  J, "Tide's low this morning. You can see the sill.", 0, []),
 (5.6,  M, "It's not a big harbour. It was never a big harbour.", 0, []),
 (6.6,  J, "The lights are on a timer now. Used to be manual.", 0, []),
 (7.6,  M, "My grandfather was here too, but he was crew, not skipper.", 0, []),
 (8.6,  I, "Sorry, one second, there's a van going past.", 0, []),
 (8.8,  M, "Shall I wait? I'll wait.", 0, ["false_start"]),
 (9.0,  J, "The quota paperwork goes to Reykjavik and comes back six weeks later.", 0, []),
 (10.6, M, "We used to land haddock mostly. Some cod. Depends on the year.", 0, []),
 (11.4, J, "There's a chandlery on the corner. Closed Tuesdays.", 0, []),
 (12.6, M, "You'd get maybe two hundred days a year you could go out. Weather.", 0, []),
 (13.7, J, "I keep the logbooks. All of them, back to when I started.", 0, []),
 (14.8, M, "It's not that people don't care. It's that caring isn't a submission.", 0, []),
 (15.9, I, "Tell me about the ice plant.", 0, []),
 (16.8, M, "The co-op ran it. Six of us on the committee.", 0, []),
 (17.8, J, "Electricity bill on that thing was extraordinary.", 0, []),
 (18.9, M, "Um, what was the question? Sorry.", 0, ["filler", "false_start"]),
 (20.0, J, "You have to sound the horn before the gate moves. Regulation.", 0, []),
 (20.9, M, "My husband worked at the plant. Different plant, up the road.", 0, []),
 (21.9, I, "Can we go back to the survey for a moment?", 0, []),
 (22.8, M, "It's forty pages and about six of them matter.", 0, []),
 (23.8, J, "I read all forty. Twice.", 0, []),
 (24.6, M, "There's a consultation window. It closed in June.", 0, []),
 (25.8, J, "I did submit. I have the reference number here somewhere.", 0, []),
 (26.8, M, "People assume it's romantic. It's cold and it's wet and the money is bad.", 0, []),
 (27.9, J, "The moorings are numbered but the numbers don't run in order. Never have.", 0, []),
 (29.0, M, "I sold the small boat in twenty-three. Kept the Sigrun.", 0, []),
 (30.0, I, "Is there anyone else we should talk to?", 0, []),
 (30.2, M, "Talk to Erla. She'll tell you about the co-op.", 0, []),
 (31.0, J, "Erla's in Akureyri now, mind.", 0, []),
 (32.0, M, "The nets go to a yard in Denmark. They pay by weight.", 0, []),
 (33.0, J, "I've got a key to everything here. Every door.", 0, []),
 (34.0, M, "It's not a museum. That's what I'd say to them. It's not a museum yet.", 0, []),
 (35.0, J, "The tide gauge is the oldest instrument on the coast, apparently.", 0, []),
 (36.0, M, "You want the truth, I've stopped reading the letters.", 0, []),
 (37.0, J, "Somebody suggested a heritage trail. I didn't say anything.", 0, []),
 (38.8, M, "My father would have known who to ring. I don't.", 0, []),
 (39.5, J, "Anyway. That's the harbour.", 0, []),
]

beats, human = [], []
for i, (mins, spk, text, used, flags) in enumerate(B):
    start = START + int(mins * 60 * FPS)
    dur = max(int(1.6 * FPS), round(len(text.split()) / WPS * FPS))
    beats.append({"id": f"b{i:03d}", "start": start, "end": start + dur,
                  "speaker": spk, "text": text, "flags": flags})
    if used:
        human.append([start, start + dur])

out = {
  "name": "harbour-lights-ep3",
  "fps": FPS,
  "notes": ("Hand-authored fixture. ~40 min of interview, human web cut of "
            f"{len(human)} beats. Synthetic material cannot validate the "
            "product — only real pairs can. This exists to prove the harness "
            "and to check the metric discriminates."),
  "beats": beats,
  "human_cut": human,
}
p = pathlib.Path(__file__).parent / "harbour.json"
p.write_text(json.dumps(out, indent=1))
total = sum(b["end"] - b["start"] for b in beats)
hum = sum(e - s for s, e in human)
print(f"beats {len(beats)}  source {total/FPS/60:.1f} min  "
      f"human cut {hum/FPS/60:.1f} min ({len(human)} beats, "
      f"{100*hum/total:.0f}% of spoken material)")
