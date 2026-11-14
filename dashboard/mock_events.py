import json
import os
import random

PIECES = 256
BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "mock_run")

random.seed(1)
nodes = ["10.0.1." + str(i) for i in range(11, 16)] + ["10.0.2." + str(i) for i in range(11, 16)]
os.makedirs(OUT, exist_ok=True)

for node in nodes:
    events = [{"t": 0.0, "node": node, "type": "join"}]
    order = random.sample(range(PIECES), PIECES)
    speed = random.uniform(2, 6)
    have = 0
    t = 0
    while have < PIECES:
        t += 1
        gained = min(PIECES - have, int(speed + random.uniform(-1, 1)) + 1)
        for _ in range(gained):
            events.append({"t": t, "node": node, "type": "have", "piece": order[have]})
            have += 1
        events.append({"t": t, "node": node, "type": "rate", "down_bps": gained * 256 * 1024 * 8})
    events.append({"t": t, "node": node, "type": "complete"})
    with open(os.path.join(OUT, "events-" + node + ".jsonl"), "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

print("wrote", len(nodes), "event files to", OUT)
