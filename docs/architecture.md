# Architecture

## Topology

```
        LAN C (origin) 10.0.3.0/24
        [Tracker .10] [Seed .11] [Dashboard .12]
                       |
LAN A 10.0.1.0/24 --- [ R1 ] ======= WAN 10.0.12.0/30 ======= [ R2 ] --- LAN B 10.0.2.0/24
 Leechers .11-.15      .1    tc netem + tbf here (.1 / .2)       .1      Leechers .11-.15
```

## Addressing plan

| Segment | Subnet | Gateway | Hosts |
|---|---|---|---|
| LAN A | 10.0.1.0/24 | R1 10.0.1.1 | Leechers .11-.15 |
| LAN B | 10.0.2.0/24 | R2 10.0.2.1 | Leechers .11-.15 |
| LAN C | 10.0.3.0/24 | R1 10.0.3.1 | Tracker .10, Seed .11, Dashboard .12 |
| WAN | 10.0.12.0/30 | n/a | R1 .1, R2 .2 |

## Ports

| Service | Port |
|---|---|
| Tracker (HTTP) | 8000/TCP |
| Peer protocol, TCP baseline | 6881/TCP |
| Peer protocol over MurmurTP | 6882/UDP |
| Dashboard | 8080/TCP |
