"""
Verifies the actual boolean behavior of is_explicit_reset_trigger() at
whatever RESET_TRIGGER_THRESHOLD is currently set in topic_reset_trigger.py
(0.80 as of this script) — as opposed to calibrate_layer_a.py, which
only reports the raw score gap and doesn't reflect a deliberately
chosen precision/recall tradeoff.

Run:
    PYTHONPATH=src python3 tools/verify_layer_a_threshold.py
"""

from topic_reset_trigger import is_explicit_reset_trigger, RESET_TRIGGER_THRESHOLD

SHOULD_TRIGGER = [
    "schimbam subiectul",
    "hai sa trecem la altceva",
    "gata cu asta, vreau sa te intreb altceva",
    "las-o balta cu freelance-ul, ce ma intrebai despre WING",
    "ok alt subiect acum",
    "nu mai conteaza ce am zis, uite ce vreau de fapt",
]

SHOULD_NOT_TRIGGER = [
    "care e subiectul discutiei noastre de azi",
    "am un subiect nou pentru eseul de facultate",
    "cat percep pentru un site web",
    "probleme cu mixerul audio la biserica",
    "ce mancare sa gatesc diseara",
    "explica-mi cum functioneaza cache-ul TTL",
]

print(f"RESET_TRIGGER_THRESHOLD = {RESET_TRIGGER_THRESHOLD}\n")

print("SHOULD TRIGGER (want: True)")
tp = 0
for phrase in SHOULD_TRIGGER:
    result = is_explicit_reset_trigger(phrase)
    tp += result
    print(f"  {'OK  ' if result else 'MISS'}  {result}  {phrase!r}")

print(f"\n-> {tp}/{len(SHOULD_TRIGGER)} real triggers caught\n")

print("SHOULD NOT TRIGGER (want: False)")
fp = 0
for phrase in SHOULD_NOT_TRIGGER:
    result = is_explicit_reset_trigger(phrase)
    fp += result
    print(f"  {'BAD ' if result else 'OK  '}  {result}  {phrase!r}")

print(f"\n-> {fp}/{len(SHOULD_NOT_TRIGGER)} false positives")
