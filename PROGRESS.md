# Weekly progress

One short line per person per week.

17 Nov 2026: Sprint 0 complete per team confirmation. A1.1 merged in PR #7
(`14677e3`), A1.2 in PR #8 (`0760813`), and A1.3 in PR #9 (`ce0c221`).
A1.4 merged in PR #10 (`d3cca91`), A1.5 in PR #11 (`1d152f5`).
A1.6 stop-and-wait UDP stage merged in PR #12 (`1bbcd4a`); acceptance:
1 MiB at 10% loss in both directions, SHA-256 verified.
H1.1 peer wire codec merged in PR #13 (`d38b41d`).
H1.2 deterministic file generator and metadata tool merged in PR #14 (`de760e8`).
H1.3 tracker server and async client merged in PR #15 (`78e2cba`).
H1.4 disk-backed piece store merged in PR #16 (`3bb4b4d`).
H2.1 single TCP download merged in PR #17 (`c64f3cf`). I1 verified on localhost:
64 MiB over real TCP, all 256 piece hashes and whole-file SHA-256 matched.
H2.2 multi-peer transfers merged in PR #18 (`418bbe5`).
H2.3 client-server mode merged in PR #19 (`8ca36a8`).
H2.4 peer event logging merged in PR #20 (`dbf15c8`); local main also includes
dashboard styling commit `f35f6cd`.
H2.5 whole-swarm Loopback tests merged in PR #21 (`ac7eed2`).
A2.1 algorithm integration merged in PR #22 (`94fbda3`). SwarmNode
uses live rarest-first selection, partial-piece priority, exclusive reservations,
configurable 1..32 request windows (default 16), and timed 4+1 upload slots.
The swarm CLI reads pipeline/choking settings; piece_select events expose choices.
A2.1 focused tests: 13 passed. One seed and ten leechers still pass in swarm and
client-server modes with independent hashes, payload accounting, and cleanup.
CI payload: 2 MiB + 37 bytes; each scenario asserts a runtime below 30 seconds.
A2.2 first step merged in PR #23 (`10b0310`). Separate Stage 2 UDP
transport provides a fixed 1..32-packet send window (default 16), cumulative
ACKs, bounded out-of-order buffering, and timeout retransmission of the
unacknowledged window. Stage 1 remains available for regression/comparison.
Stage 2 focused tests: 21 passed in 13.66s. Verified 16 MiB at 5% loss in both
directions with matching SHA-256; Stage 2 beats Stage 1 in a separate controlled
delay test.
A2.2 Stage 3 merged in PR #24 (`0fbf831`). Separate selective-repeat
transport uses the v1 SACK bitmap, a bounded sender scoreboard, independent
packet deadlines, selective timeout retries and the three-later-SACK gap rule.
Stage 3 focused tests: 15 passed in 19.03s. A2.2 acceptance: 64 MiB over localhost
UDP with 5% loss, 5% reorder and 1% duplication on both endpoints, SHA-256 and
payload counters verified (acceptance case 17.76s).
A2.3 randomized transport properties and cancellation fix merged in PR #25
(`a7a0f75`) and pulled into local main. Both CI checks passed per team confirmation.
Initial focused run: 8 passed in 13.90s, covering 108 generated and 9 explicit examples.
Verified exact duplex streams across Stages 1..3 with independent impairments,
MSS/send/read boundaries, small buffers, stable EOF and payload accounting;
interruption preserves only valid prefixes, total loss times out, and resources
are cleaned up. Initial Windows full suite: 407 passed, 2 Linux TCP tests skipped.
PR #25 Linux/Python 3.11 CI then exposed a lost-cancellation race in Stage 3's
ACK wait. Replaced that wait with cancellation-propagating asyncio.wait and
explicit child-task cleanup; added two deterministic ACK/cancel regressions
and retained both CI seeds. At merge, properties covered 108 generated + 13 explicit
examples. Congestion/flow control remain later work.
Cancellation-fix verification: 25 focused tests passed; Windows full suite
409 passed, 2 Linux TCP tests skipped in 115.03s. Python 3.10 syntax checks pass.
The team confirmed the fixed commit's Linux/Python 3.11 CI passed before merging;
that CI run has not been independently inspected here.
A3.1 timing portion merged in PR #26 (`5f74697`), pulled into local main.
Separate Stage 4 AdaptiveRtoTransport provides:
Jacobson/Karels SRTT/RTTVAR, 1s initial RTO with 200ms..5s bounds, Karn's rule
for timeout and fast retries, and exponential backoff once per timeout round.
DATA deadlines remain independent; FIN uses learned timing. Stats expose SRTT,
variation, current RTO and sample count. Stage 1..3 baselines remain available.
Stage 4 is included in randomized duplex/total-loss properties and deterministic
ACK/cancel regressions. At the Stage 4 commit, verification comprised
41 new timing cases plus 14 property/cancellation cases.
Full Windows/Python 3.14 suite: 454 passed, 2 Linux TCP tests skipped in 116.59s,
with warnings treated as errors. Python 3.10 syntax checks passed.
A3.1 congestion portion merged in PR #27 (`7d2fa45`), pulled into local main.
Separate Stage 5 CongestionTransport provides:
initial 10-packet window, slow start, additive increase, one SACK reduction per
recovery boundary and one reset per timeout round. New DATA admission respects
both floor(cwnd) and the configured sequence horizon. Stats report cwnd in bytes.
Focused congestion tests: 13 passed in 2.93s. The 1 MiB acceptance case at 2%
outgoing loss on both localhost UDP endpoints verified SHA-256 and repeated
window growth/reductions; CSV trace is saved in pytest's temporary directory.
Randomized duplex/total-loss properties now cover Stages 1..5; deterministic
ACK/cancel regressions at that commit covered Stages 3..5. A3.1 is merged.
No routed E4 or fairness result is claimed.
Stage 5 Windows/Python 3.14 verification: 471 passed, 2 existing Linux TCP skips
in 123.88s with warnings treated as errors. The five changed Python files pass
Python 3.10 syntax checks. Git diff whitespace checks pass.
A3.2 first portion merged in PR #28 (`29eaedc`), pulled into local main.
Stage 6 FlowControlTransport provides advertised
receive slots limit new sends alongside cwnd and the sequence horizon. Reads
reopen the window with ACK updates; empty-DATA persist probes use the frozen
one-second default without consuming bytes/sequence numbers. Zero-window DATA
timers pause, while inactivity still bounds stalled readers. Tests cover lost
updates/replies, partial-slot reads, buffer bounds, cancellation, shutdown and
FIN with unread data. Properties now cover Stages 1..6 and ACK/cancel regressions
Stages 3..6.
Stage 6 Windows/Python 3.14 full regression run: 496 passed, 2 existing Linux TCP
skips in 136.14s. Final review added the TIME_WAIT idle-timer guard/regression;
final focused verification: 44 passed in 31.75s. Both runs used warnings as errors.
Five changed Python files pass Python 3.10 syntax checks; Linux CI pending.
A3.2 Stage 7 merged in PR #29 (`8fe6ed7`), pulled into local main.
It provides public Contract 2 MurmurTransport/MurmurConnection,
validated transport factory, TCP/MurmurTP swarm CLI selection and matching default
ports. Default config now selects TCP (the CLI previously ignored loopback).
Existing PeerEvents emits real MurmurTP metrics and exact final retransmission
summaries without a second sampler. Corrected a HAVE/NOT_INTERESTED backpressure
race exposed by the larger UDP swarm, with deterministic regression coverage.
Focused verification: 41 passed in 45.15s; both distribution modes passed the same
one-seed/ten-leecher test on Loopback and localhost MurmurTP (2 MiB + 37 bytes).
Independent hashes, exact payload accounting, relay uploads, zero client-server
leecher uploads, logs and cleanup verified. Smaller relay/event tests also pass
at 5% loss, 5% reorder and 1% duplication. A3.2 local implementation is complete;
Stage 7 is merged. Routed A3.3 evidence is outstanding.
Stage 7 full Windows/Python 3.14 regression: 523 passed, 2 existing Linux TCP
skips in 182.23s with warnings treated as errors. Nine changed Python files pass
Python 3.10 syntax checks; Git whitespace checks pass.
18 Nov 2026: A2.4 offline aggregation/plotting merged in PR #30 (`650bdf2`)
and pulled into local main. Implemented against explicit mock
CSV metrics. Validates configuration/run identities, excludes warm-ups, preserves
unknown values and reports observed/missing counts with mean, sample SD and min/max.
Subjects keep per-peer/flow metrics separate. PNG plots reject mixed data kinds
or fixed conditions and label mock data prominently. Sample table contains 30
rows; the PNG was generated and visually inspected. This does not run experiments
or convert raw JSONL; harness/export and real results remain pending.
Matplotlib was already available in system Python; installation
was not approved, so verification uses that existing interpreter for plotting.
Analysis verification: 30 passed with warnings as errors. Full Windows run:
551 passed, 2 Linux TCP skips, 2 existing MurmurTP acceptance deadline failures
(64 MiB Stage 3 and client-server whole-swarm). Full regression is not green;
transport code and test deadlines were unchanged by A2.4.
Both deadline failures reproduced in a separate two-test run during A2.4.
Timeout follow-up: idle swarm peers now reannounce their interest state every
3 seconds without changing upload slots or payload accounting. This prevents
MurmurTP inactivity expiry while waiting for a choking slot. Silent neighbors
still expire; link shutdown joins the keepalive worker. The CLI exposes
keepalive_interval, which must be comfortably below custom transport idle limits.
Both original cases subsequently passed: 64 MiB Stage 3 in 39.88s and ten-leecher
client-server MurmurTP in 16.72s. Their 120s/60s deadlines were not changed.
The earlier Stage 3 slowdown did not reproduce in this run; no transport change
is claimed to fix its underlying cause. Keepalive/algorithm checks: 20 passed.
Final Windows/Python 3.14 full regression: 559 passed, 2 existing Linux TCP
skips in 197.16s, warnings treated as errors. In that full run, Stage 3 took
49.47s and client-server MurmurTP 15.04s. CLI keepalive configuration check
also passed separately after its final assertion was added. Four changed/new
Python files pass Python 3.10 syntax checks; whitespace checks pass.
Timeout follow-up merged in PR #31 (`3fd6643`) and pulled into local main.
18 Nov 2026: A3.4 Wireshark Lua dissector implemented and verified with real
TShark against synthetic pcapng fixtures. Decodes every v1 header field,
individual flags, SACK sequences, payload and the odd-offset Internet checksum.
Handles malformed/truncated packets without Lua range errors; supports Decode
As on custom UDP ports. All 16 focused tests passed on TShark 4.6.8/Lua 5.4.6.
CI now installs TShark and requires these tests. Generated captures are marked
synthetic and ignored; real annotated M3.2 captures/A3.3 topology remain pending.
A3.4 review, Linux CI and merge pending.
Final Windows/Python 3.14 full regression: 575 passed, 2 existing Linux TCP
skips in 107.12s with warnings as errors. The actual Lua/TShark checks ran;
they were not skipped. Three new Python files pass Python 3.10 syntax checks.
A3.4 CI follow-up: supplied Linux/Python 3.11 log had 16 TShark Lua-file
access denials and one five-second swarm shutdown timeout. CI is now pinned to
ubuntu-24.04 and prints TShark's version. Peer send/handshake and block-request
waits preserve caller cancellation on simultaneous inner completion, retaining
the original timeout thresholds. Six new deterministic cancellation cases added;
focused Windows run: 32 passed in 21.02s. Updated Linux CI remains pending.
Follow-up full verification exposed three existing test ordering assumptions;
fixtures now synchronize on slot replacement/block persistence and consume a
client greeting before closing with an invalid reply. No assertions or deadlines
were relaxed. Focused peer/cancellation verification: 60 passed in 6.88s.
Final CI-follow-up local full suite: 581 passed, 2 existing Linux TCP skips in
115.63s with warnings as errors. Client-server MurmurTP acceptance passed in
7.98s including unchanged cleanup limit. Seven changed/new Python files pass
Python 3.10 syntax checks. Updated Linux CI/review/merge still pending.
A3.4 dissector and CI follow-up merged in PR #32 (`4e2fb59`) and pulled into
clean local main. Prior CI history above records pre-merge verification.
18 Nov 2026: A4.1 local flowbench foundation implemented: one bulk file over
Contract 2 TCP/MurmurTP (Loopback tests), readiness/verified SHA-256 receipt,
bounded streaming, monotonic duration/goodput and Contract 3 rate/transport/
summary events. Setup, transfer and close have a shared deadline; cancellation
joins workers and closes the owned endpoint. Focused tests: 24 passed.
This does not complete E4/E5: routed Linux runs, concurrent-flow scheduling,
steady-state fairness, ss evidence, normalized metric export and analysis remain
pending. Flowbench review/Linux CI/merge pending.
Final local verification: 24 focused tests passed in 4.86s, including final CLI
transport flags. Full Windows/Python 3.14 suite: 605 passed, 2 existing Linux TCP
skips in 196.43s, warnings as errors. Two new Python files pass Python 3.10 syntax
checks; Git whitespace checks pass. These are functional localhost checks, not
routed E4/E5 measurements.
18 Nov 2026: A4.1 CI follow-up: supplied push log reports 606 passed and one
MurmurTP swarm failure. All files completed, but a late PIECE was rejected and
the zero-waste accounting wait expired. Two deterministic regressions reproduce
the same protocol error before the fix: CHOKE, reissue the same block, then
receive both replies (including repeated abandonments). Track bounded reply
allowances per abandoned request; accept the block once and separately count
authorized late payload as bytes_discarded. Unsolicited replies and more than
64 unresolved abandonments still disconnect. Whole-swarm accounting now checks
sent = accepted + discarded, retaining hash, useful-byte, cleanup and deadline
checks. A real two-node forced retry verifies the extra block and exact totals.
Focused swarm/flowbench verification: 64 passed in 43.69s; the added 64/65 limit
check and both reissue regressions then passed together (3 passed in 2.12s).
Final full Windows/Python 3.14 regression: 609 passed, 2 existing Linux TCP
skips in 224.22s, warnings as errors. Whole-swarm MurmurTP took 20.52s (swarm)
and 21.48s (client-server), within unchanged deadlines. Four changed Python
files pass Python 3.10 syntax checks; Git whitespace checks pass. Updated Linux
CI, review and merge were pending at the time of that run.
M1.1 follow-up: PR #33 is merged at cead7b7. Majid supplied Ubuntu 24.04.3
VM output (kernel 6.17.0-14-generic) showing ip, bridge, ethtool, ping, make,
tc, traceroute and iperf3 installed, a successful sudo network namespace
probe, and a clean Ubuntu clone at cead7b7. Routed topology setup, namespace
execution, teardown and an Ubuntu acceptance script are now implemented.
Majid ran `sudo make test-topo` in that VM against the M1.1 patch.
All three checks passed. With five leechers per LAN, cross-LAN and origin
pings succeeded, router lookups used the expected WAN next hops, and the
sampled interfaces had TSO, GSO and GRO disabled. Setup then rebuilt the
lab with ten leechers per LAN and the added hosts passed the connectivity
checks. After manual removal of R2, two teardowns restored the original
named namespace inventory. M1.1 merged in PR #34 (`27fdede`), pulled on
Windows and Ubuntu.
M1.2: added WAN delay/loss and rate controls, the fixed seed uplink cap,
apply/clear Make targets, and a routed ping/iperf3 acceptance check.
Majid's Ubuntu run passed. WAN receiver goodput was 8.30 and 8.60 Mbit/s
at a 10 Mbit/s cap, then 4.88 Mbit/s with the 5 Mbit/s override. The
seed-to-LAN-A path delivered 9.53 Mbit/s. Both routers recorded netem drops.
Repeated clear restored the default queues and teardown completed. The
[topology guide](infra/README.md#m12-acceptance-on-ubuntu) records the RTT
and loss measurements. M1.2 merged in PR #35 (`d590b24`).
M1.3: at that revision the focused 64 MiB localhost TCP I1 test passed
on Windows. Majid's Ubuntu route tables and ICMP traceroute confirmed
a1 → R1 → R2 → b1. Five of five pings returned, with 36.775 ms mean RTT.
The [Gate S1 evidence](captures/s1/README.md) includes these observations
and the earlier M1.2 iperf3 measurements. All four Ubuntu screenshots were
checked against the terminal output. The technical Gate S1 checks passed;
the M1.3 evidence is ready for team review and gate tagging after merge.
Remaining transport work, tracker discovery, routed TCP swarm
evidence, harness/dashboard integration, and the remaining Gate S2 checks are pending.

M1.3 evidence merged in PR #36 (`ad4fbef`). Majid reviewed the M2.1 dashboard
locally and approved it for push. The [dashboard](dashboard/README.md) follows the supplied HTML
design and reads incremental peer events. Windows localhost TCP and MurmurTP
review transfers each produced two complete, SHA-256-verified downloads.
The focused dashboard and peer event tests passed (16 tests). Chrome rendered
the recorded MurmurTP output. Routed dashboard integration and Gate S2 remain open.

M2.1 merged in PR #37 (`57068b5`). M2.2 adds the
[routed TCP runner](experiments/README.md#m22-routed-tcp-swarm) and tracker
discovery for swarm peers. On Windows, one seed and ten leechers used real
localhost TCP and an HTTP tracker. All ten downloaded files matched the seed.
The Windows regression suite passed 619 tests with three platform skips.
Majid's Ubuntu run `20261119T201545Z-e1a12b8c` passed M2.2. One TCP seed
served a swarm of five leechers per LAN. The WAN used 10 ms delay per
direction, 0% configured loss and a 10 Mbit/s cap, with a 10 Mbit/s seed uplink.
All ten 67,108,864-byte files matched the seed's SHA-256
(`45581d305941fbabc46eac1027f88e99fdc8dfd3de474343dc93a61766d2fd61`).
The leechers uploaded 589,758,464 bytes in total and topology teardown completed.
Gate S2 remains open, including packet capture and dashboard evidence from a routed run.

M2.2 merged in PR #38 (`ae6e936`). M2.3 adds supervised tcpdump capture on
R1's WAN and verifies the saved TCP packet count after the peers stop.
Windows checks exercised capture supervision and the filter on offline frames.
The Windows regression suite passed 626 tests with three platform skips.
Majid's Ubuntu run `20261119T203919Z-a3653c43` passed M2.3. The runner
read back 41,389 swarm TCP packets from the 5,005,461-byte R1 WAN capture.
All ten leechers verified the 8 MiB file, uploaded 69,828,608 bytes in
aggregate, and the runner removed the topology. The
[reviewed 200-frame sample](captures/s2-tcp-wan.pcapng) shows a TCP
handshake between leechers on opposite LANs and data in both directions.
M2.3 merged in PR #39 (`ab7d63c`). Majid reviewed the saved 64 MiB
routed TCP run in the Ubuntu dashboard. It showed all ten downloads complete
and all 256 pieces verified for `10.0.1.11`. The same leecher's log records
piece selections with availability 1 and 4 and a cross-LAN unchoke followed
by a choke of the same peer. Majid confirmed the fresh-checkout GitHub
Actions run succeeded for merged main at `ab7d63c`. All technical Gate S2
checks passed. [Gate S2 evidence and CI run](captures/README.md#gate-s2-ci)

Gate S2 evidence merged in PR #40 (`5748368`). The I3 runner now selects TCP
or MurmurTP, configures per-direction WAN loss, captures the selected swarm
port and verifies the observed transport on each peer. On Windows, ten
tracker-discovered leechers verified their files over localhost MurmurTP,
both with clean sockets and with 5% outgoing shim loss. The lossy test used
a 131,109-byte payload and recorded retransmissions. The Windows regression
suite passed 629 tests with four platform skips. On Ubuntu, all ten
leechers verified the 8 MiB smoke transfer and the 64 MiB transfer with
5% configured loss in each WAN direction. Both captures passed readback
and both topologies were removed. [I3 run IDs, counts and hashes](experiments/README.md#ubuntu-i3-acceptance)

I3 merged in PR #41 (`a783cfe`). M3.1 adds a time slider and variable-speed
playback for saved runs, plus a per-connection overlay of congestion window,
RTT and retransmissions. The Windows screenshots of the routed Ubuntu recording
show 10/10 downloads and 256/256 pieces at 678.67 seconds. The header now
identifies the recorded protocol and the end-of-run button reads Replay.
During regression testing, the clean ten-leecher MurmurTP test timed out on
Windows. Increasing the shared UDP socket receive buffer from 64 KiB to
1 MiB let the same test finish in 12 seconds with its original deadline.
The full Windows suite then passed 631 tests with five skips in 211.92 seconds.

M3.1 merged in PR #42 (`0412c71`). M3.2 documents five exchanges from
the routed 5% loss run. Each Wireshark screenshot matches its annotated
frames in the [capture guide](captures/README.md#gate-s3-murmurtp-exchanges).
The protocol specification review remains open for Gate S3.

M3.2 merged in PR #43 (`e02553f`). M3.3 adds per-trial checkpoints, bounded
setup and capture checks, and a warm-up followed by three recorded runs.
All 16 Windows checkpoint and capture checks passed. The wider Windows run
had 633 passes, five skips and four failures. Three failing cases passed on
retry or in isolation. The clean ten-leecher localhost MurmurTP timeout also
reproduced on an untouched export of `e02553f`. The routed Ubuntu TCP resume
check passed. The runner skipped the verified warm-up, retried the first
measurement in attempt 2 and verified ten downloads in each of three
measurements. [Ubuntu resume evidence](experiments/README.md#ubuntu-interruption-and-resume-check)

M3.3 merged in PR #44 (`be0bd8b`). M4.1 adds E1/E2 sweep and report
generation. The 51 focused Windows sweep, series, capture and analysis tests
passed. On the prior Ubuntu MurmurTP 5% loss logs, the exporter read ten peer
goodputs, 676.91 s distribution time and 15,183 total retransmissions.
The [8 MiB Ubuntu E2 TCP pilot](experiments/README.md#ubuntu-e2-pilot-result)
passed at 100 ms configured RTT. Mean distribution time was 30.549 s across
three recorded trials.
The opt-in compact sweep retains the verified seed after each successful trial.
All 56 focused Windows sweep, series, capture and analysis checks passed,
including interrupted cleanup and report generation from compact trials.
The Ubuntu compact pilot passed at `ebe8b38`. All four attempts verified
ten downloads each and retained one 8 MiB seed each. The compact directory
used 56 MiB compared with 374 MiB for the earlier pilot.
The final compact Ubuntu E1/E2 sweep at `ebe8b38` completed all 12 conditions.
After restart the runner reused 25 verified trials and retried the interrupted
measurement. All 36 recorded distribution times are present. The runner saved
both CSVs and plots. The full directory occupied 5.4 GiB.
See [distribution times](experiments/README.md#final-64-mib-distribution-times).
The [measured results](results/e1-e2/README.md) include the 14 figures and
13 checkpoints. All 48 passed attempts reconciled with the normalized rows.
Re-aggregating 1,104 metric rows reproduced the 276-row summary byte for byte.
The M4.1 evidence and analysis are ready for PR review.

M4.1 merged in PR #45 (`67b8190`). E3 adds compact, resumable TCP swarm
and client-server trials at 2/5/10/20 active leechers, with per-attempt
R1 WAN byte deltas, seed upload share and a WAN savings comparison. The
Ubuntu five-leecher pilot passed in both modes. The seed supplied 31.23%
of uploaded payload in swarm mode and 100% in client-server mode.
The [pilot results](experiments/README.md#five-leecher-ubuntu-pilot) precede
the 64 MiB scaling runs. All three N=10 swarm measurements finished. The
N=10 client-server warm-up was interrupted after nine downloads completed.
Peer a3 verified 48 of 256 pieces before its seed session reached the
abandoned-request limit. Direct clients now retry the origin connection
within the transfer deadline. The updated ten-leecher pilot passed, followed
by all 32 trials in the fresh 64 MiB Ubuntu sweep. At 20 leechers the swarm
averaged 149.01 s to the last completion and used 64.32% fewer WAN bytes
than client-server. The [E3 results](results/e3/README.md) cover all four counts.

E3 merged in PR #46 (`94bb775`). The E4 runner connects the bulk-flow
benchmark to the routed lab, samples TCP Reno, TCP CUBIC and MurmurTP, and
exports receiver goodput, sender congestion windows and RTT error against
concurrent ping samples. Windows regression: 664 passed and 6 skipped with
warnings treated as errors. The [8 MiB Ubuntu pilot](experiments/README.md#e4-ubuntu-pilot-results)
passed all 12 transfers and produced all six expected summary rows.
The [64 MiB loss sweep](results/e4/README.md) completed on Ubuntu with
36 recorded transfers and three observations in every summary row. The Windows
import reconciled all 24 summary rows with the trial checkpoints and checked
7,407 recorded sender samples, including 7,347 paired with a ping RTT.

E4 merged in PR #47 (`4adb3b9`). The [E5 runner](experiments/README.md#e5-competing-flow-fairness)
starts two verified flows on the shared WAN and measures Jain fairness and
receiver goodput during their common transfer interval. The 8 MiB Ubuntu
pilot and [final 64 MiB sweep](results/e5/README.md) each passed all 12 trials.
The final recorded trials gave a mean Jain index of 0.5055 for MurmurTP
with CUBIC, 1.0000 (rounded) for two MurmurTP flows and 0.9931 for two CUBIC flows.
On Windows, all 41
focused E4/E5 and flowbench tests passed. The full suite had 672 passes,
six skips and one timeout in the ten-leecher MurmurTP discovery test with
zero configured loss. The same case timed out on retry and on the merged
E4 revision. Its cause remains open.

E5 merged in PR #48 (`35e205f`). M4.2 adds a sequential E1–E5 Make target
and a [fresh Ubuntu reproduction guide](docs/reproduce.md). The 35 published
conditions have three recorded trials each. Windows GNU Make checks confirmed
stage order under parallel Make, stopping on failure, path and size overrides,
and standalone target defaults. The focused experiment tests passed 38 cases
with one Linux-only skip. Majid verified a new checkout at `3c2348a` on his
existing Ubuntu 24.04.3 VM. Pytest reported 677 passed and two skipped.
Topology checks passed, and shaping passed on its third attempt following
two throughput failures. The E1 TCP pilot verified all four trials, with
mean recorded distribution time 30.642 seconds and sample SD 2.458439 seconds.
The [reproduction record](docs/reproduce.md#m42-checkout-verification)
retains the failed measurements and diagnostic. Fresh-VM acceptance and a
complete run of the combined command remain pending.

S5 report draft: [docs/report.md](docs/report.md) brings together architecture,
swarm and transport design, methodology, E1–E5 results and annotated packet
evidence. It retains the negative two-peer WAN savings, mixed-flow fairness
limitation, failed shaping checks and unresolved Windows timeout. The draft
uses existing measurements; no experiments were rerun for report preparation.
Report review, independent protocol review, fresh-VM acceptance, the complete
combined experiment run and final demo/release work remain pending.

Windows timeout investigation at `28c0f6c`: the zero-loss ten-leecher
MurmurTP discovery test failed in 68.57 seconds and also failed with the
Windows selector loop. The TCP control passed in 22.94 seconds. Profiling
found 239 synchronous log refreshes occupying 44.70 seconds on the peer
event loop. Test monitoring now runs in a worker thread; the isolated
MurmurTP case passed in 52.21 seconds with unchanged deadlines and payload.
Transport code and published experiment measurements are unchanged.
The complete experiment-runner test file then passed seven tests with two
Linux-only subprocess skips in 68.21 seconds, including TCP, zero-loss
MurmurTP and 5%-loss MurmurTP. Full-suite release validation remains pending.
The subsequent full Windows run reported 670 passed, eight skipped and one
failure in 323.58 seconds. The MurmurTP discovery cases passed. The remaining
failure was `test_cli_table_and_real_png`: Matplotlib was absent from the
active virtual environment. Installing the declared dependency was blocked
by sandbox network restrictions and the escalation request was declined.
Majid installed Matplotlib and reran the analysis tests. The first rerun
encountered pytest temporary-directory permissions; using a unique temporary
directory and disabling pytest's cache gave 30 passed in 16.33 seconds,
including the previously failing PNG test. No failures remain from the full
run without a passing retest; this is not a new all-green full-suite run.
Review of skipped checks and final release validation remain pending.

Skipped-test audit on `89f9cec`: with Matplotlib installed, the E4 figure
and E5 reporting tests passed. The targeted Windows audit reported 23 passed
and six skipped in 16.90 seconds: five Linux-only cases and the opt-in Chrome
check. Enabling the real headless Chrome check then gave one pass in 5.11
seconds, covering replay seek, play/pause, restart, overlay and recorded
transport display on generated logs. This is automated control verification,
not a new routed transfer or a demo recording. Majid then ran the five
platform-specific cases in Ubuntu: five passed in 11.09 seconds, covering
Reno/CUBIC socket options, TCP/MurmurTP subprocess supervision and the E3
direct-seed runner. Every case skipped in the earlier Windows run now has
a passing targeted result on an appropriate platform. Fresh-VM and
combined-sweep acceptance remain separate outstanding work.
