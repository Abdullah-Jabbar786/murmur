# Murmur project context

Course project (Computer Networks) combining #1 (BitTorrent-style P2P) and #15 (Reliable UDP transport). Team of 3, about 8 weeks.

## Names
- Murmur: the whole project
- MurmurTP: the custom reliable transport that rides on UDP

## Roles
- A Transport: MurmurTP, congestion control, experiments E4 and E5
- B Swarm: tracker, peers, rarest-first, choking, experiment E3
- C Infra: topology, netem, automation, dashboard, pcaps, experiments E1 and E2

## Swarm
- 64 MiB test file, 256 KiB pieces, 16 KiB blocks, SHA-256 per piece
- Messages: HANDSHAKE, BITFIELD, HAVE, INTERESTED, CHOKE, UNCHOKE, REQUEST, PIECE
- Rarest-first, 4 unchoke slots re-evaluated every 10 s plus 1 optimistic
- The swarm talks to a transport interface so TCP and MurmurTP swap by a config flag

## MurmurTP
- Selective repeat with SACK, Jacobson/Karels RTO, slow start plus AIMD
- Baseline for comparison: kernel TCP (Reno and CUBIC)

## Experiments (3 runs each, mean and standard deviation)
- E1: loss 0, 2, 5 percent, TCP vs MurmurTP swarm
- E2: RTT 20, 100, 200 ms
- E3: peers N = 2, 5, 10, 20, swarm vs client-server
- E4: single flow, loss 0, 1, 2, 5 percent, cwnd and goodput
- E5: two competing flows, Jain fairness

## Gates
1. Proposal plus topology and traceroute (week 1)
2. Swarm works over TCP (week 3), the rubric is safe at this point
3. Swarm runs on MurmurTP (week 5)
4. All experiment tables filled (week 6)
5. Stretch goal (week 7): DHT, encryption, or a 50-peer test
6. Report, README, demo, reproduction test (week 8)

Order never changes: TCP swarm, then transport, then experiments, then stretch.
