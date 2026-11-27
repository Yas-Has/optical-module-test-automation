# Testing

How the system was verified, including every error scenario. Design background is in [DESIGN.md](DESIGN.md).

```bash
pytest                    # 229 tests, about 10 seconds
pytest -m "not slow"      # skip the 200-run statistical check
pytest -m docker          # the Docker test (needs Docker running; about 15 seconds)
```

**Result at time of writing: 229 passed, 0 failed; Docker test passed** (Python 3.13, macOS).

---

## 1. Strategy

**The test folders mirror the code folders.** Each part is tested on its own, next to where it lives; only `tests/system/` runs the whole program.

| Tests | What they prove |
|---|---|
| `tests/core/` | Replies are judged correctly; the instrument lifecycle; roles; the registry finds a brand-new instrument folder with no other change; PyVISA over a real network socket |
| `tests/instruments/tec/`, `opm/` | Each driver's commands and reply handling (against a scripted fake instrument); each simulator follows its physics model |
| `tests/simulation/` | The device link, fault injection, simulated clock and seeds, the bench's control commands and ports, how a Docker bench is started |
| `tests/sequence/` | The temperature stability rule; the retry policy, with each fault forced on purpose |
| `tests/data/` | dBm/mW maths, statistics, inclusive limits, live recording, reports, re-processing, charts |
| `tests/config/` | Every validation rule gives its plain-language error |
| `tests/system/` | Full runs, runs with changed settings, failure scenarios, network and Docker modes, the command line, the statistical check |

Two techniques make this reliable:
- **Forcing faults on purpose.** Random faults are switched off and one specific fault is injected through the bench ("the next meter reply times out", "kill the meter after 10 readings"). Every error path is tested deliberately, not left to chance.
- **Seeds.** The same seed must give an identical raw file: between runs, and between the in-process, network and Docker modes.

---

## 2. Error scenarios (forced failures)

| Scenario | Verified outcome |
|---|---|
| One `POW:ALL?` reply lost | 8 `TIMEOUT` rows (attempt 1), 8 `VALID` rows (attempt 2); device still passes; 1 retry counted per channel |
| Two lost in a row | That reading is `TIMEOUT` on every channel; every channel fails validity |
| Whole reply unreadable | `GARBLED` rows with the damaged text kept; retried; recovers |
| One channel damaged | **Only that channel** is re-read; the other 7 keep their first reading |
| One channel impossible | `INVALID`, raw reply kept, **not retried**; only that channel fails |
| TEC reading lost | Retried once; recovers |
| Invalid TEC reading late in settling | Stability window restarts; settles later; passes |
| Weak channel (−4 dB) | All 50 readings `VALID`; channel fails on its average |
| TEC never settles (0.8 °C hot) | 2 settling attempts, verification fails, light still measured, device fails |
| One of two meters dies mid-run | Its channels: 10 valid + 40 `NOT_MEASURED`, fail. The other meter's channels pass. Status `COMPLETE_WITH_ERRORS` |
| Every meter dies, or the TEC dies | `ABORTED`; data so far kept; summary still written; every planned reading accounted for; TEC output off |
| Ctrl+C mid-run | `ABORTED`; readings so far kept; TEC output off; bench stopped |
| Network: forced timeout | A real PyVISA timeout fires; the retry recovers |
| Port already in use; bad config; Docker missing or not running | A clear message **before** any results folder is created |
| Charts fail to draw, or matplotlib missing | Run still completes with its summary; the log says why there are no charts |

---

## 3. Simulators, settings and statistics

**Simulators against their models** (`tests/instruments/*/test_simulator.py`, `tests/simulation/`): meter noise is 0.050 dB at 100 ms and halves at 400 ms (within 5%); each channel's mean matches its offset; the TEC follows its settling curve exactly (34.48 °C one time constant after starting from 25 °C); cooling the TEC from 40 to 30 °C makes the meter read 1.0 dB brighter (the device link); forcing a fault happens exactly once and doesn't shift any later random numbers.

**Runs with changed settings** (`tests/system/test_modified_settings.py`): each test edits settings as a user would, runs the whole program, and checks the data, the summary **and the charts** follow with no code change. Examples:
- tightening the limits to ±0.3 dBm fails only channel 4 (−0.40 dB), and the chart's limit lines move to ±0.3;
- allowing 5 bad readings flips the flaky meter's verdict from FAIL to PASS while the raw data stays identical (a limit changes the judgement, never the measurements);
- a 30 °C setpoint makes every channel about 1 dB brighter and moves the chart's target band;
- 20 readings per channel, a 4-channel device, and more or fewer retries each show up correctly in the files and charts.

**Statistical check** (`tests/system/test_statistics.py`): 200 seeded runs with the default fault rates. The design predicts about 66% of devices pass (0.999⁴⁰⁰ ≈ 67% with no impossible value, × about 0.99 for communication failures whose retry also fails). **Measured: 63%**, within the expected spread (±3.3%). The simulator, the retry policy and the analysis agree with the maths.

---

## 4. Manual checks

The six sample runs in `sample_results/` (listed in the [README](../README.md#sample-results)) were produced through the real network bench, and their files were inspected by hand: for example, the flaky-meter run's 492 valid, 40 timeout, 24 garbled and 5 invalid rows were each handled by the right rule. Re-running seed 42 after later changes gave **byte-for-byte identical** raw data (apart from run ID and timestamps).

The charts in each folder were added afterwards with `ate plot` and checked by eye against their run. `ate run --mode docker --seed 42` was also checked by hand: identical files to a run without Docker, and no container left afterwards.

---

## 5. Issues found by testing

1. **Temperature "verified" while still rising.** The first stability rule only required ±0.5 °C for 10 s. A controller heading for 40.8 °C spends about 11 s inside the band on the way up, so the faulty TEC wrongly passed. Fix: readings must also change by no more than 0.1 °C across the window, so the temperature must have **stopped moving**.
2. **Exactly −3.0 dBm failing.** −3 dBm converted to milliwatts and back is −3.0000000000000004. Limits are now compared at 0.000001 dB resolution, so they're truly inclusive.
3. **Two runs crashing on the same folder name.** Folder names use the time to the second plus a 4-character random code. The statistical check starts about 70 runs a second, so occasionally two drew the same code and the second run crashed (about 1 full test run in 8). Now it draws a new code; folders are still never reused. A test forces a clash, and the suite then passed 12 times in a row.
4. **Docker mode loading the wrong libraries.** The first Docker run shared the folder *containing* the `ate` package into the container. When installed, that's the folder of every installed library, built for macOS, and the Linux container failed to start (cleanly, with a clear message). Now only the `ate` package is shared, and a test checks it.

---

## 6. Known limitations

- Faults are injected into measurement queries only. Setup commands are reliable in simulation, though the drivers still retry them and fail clearly if they never answer.
- A truncated reply that still looks like a number (`0.0` from `0.04`) can't be detected without a checksum, which this command language doesn't have. The simulator never sends one.
- Real-hardware mode is implemented but, without hardware, verified only in that it uses the same PyVISA code as the network mode.
- Docker mode was verified on macOS (Docker Desktop) only, and its test isn't part of the default run.
