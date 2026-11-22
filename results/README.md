# results

The [measured E1/E2 results](e1-e2/README.md) include both CSVs, all
fourteen figures and the checkpoints from the final Ubuntu sweep. The
[E3 scaling results](e3/README.md) compare TCP swarm and client-server
distribution at four peer counts. The [E4 single-flow measurements](e4/README.md)
compare MurmurTP, Reno and CUBIC at four loss levels. The [E5 fairness results](e5/README.md)
show how two simultaneous transfers shared a 10 Mbit/s WAN.

The ignored `mock-analysis/` directory contains locally generated A2.4 sample
tables and PNGs. These are explicitly labeled mock data and must not be presented
as experiment results. Recreate them using [the analysis guide](../docs/results-analysis.md).
