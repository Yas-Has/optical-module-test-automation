# Testing Approach

How the system was verified, including error scenarios. All checks are automated and run with:

```bash
pytest                         # 208 tests, about 10 seconds
pytest tests/instruments/tec   # or just one part
```

**Result at time of writing: 208 passed, 0 failed** (Python 3.13.1, macOS). Before the charts were added, the 193 tests then in the suite passed on 12 consecutive full runs (see §8, issue 3).

---

## 1. Strategy

**The test folders mirror the program's folders.** Each part is tested next to where it lives, on its own. Only `tests/system/` tests the whole program working together.

| Test folder | Tests the part in | What it proves |
|---|---|---|
| `tests/core/` | `src/ate/core/` | Replies are judged correctly; the shared instrument lifecycle; roles; the registry finds instrument folders automatically (including a brand-new one); PyVISA over a real network socket |
| `tests/instruments/tec/` | `src/ate/instruments/tec/` | The TEC driver's commands and reply handling; the TEC simulator follows its thermal model |
| `tests/instruments/opm/` | `src/ate/instruments/opm/` | The meter driver's per-channel reply handling; the meter simulator follows its measurement model |
| `tests/simulation/` | `src/ate/simulation/` | The device link; fault injection; simulated clock and seeds; shared simulator behaviour; the bench's control commands and ports |
| `tests/sequence/` | `src/ate/sequence/` | The temperature stability rule; the retry policy |
| `tests/data/` | `src/ate/data/` | Statistics and pass/fail; live recording; reports; re-processing |
| `tests/config/` | `src/ate/config.py` | Every validation rule |
| `tests/system/` | everything together | Full runs, failure scenarios, network mode, the command line, the statistical check |

A new instrument gets its own `tests/instruments/<name>/` folder, mirroring its code folder.

Two techniques make this possible:

- **Forcing faults on purpose.** Random faults are switched off, and a specific fault is injected through the bench's test hook (`force_fault`, `kill`). In-process this is a direct call; over the network it goes through the bench's control port. Every error scenario is therefore tested deterministically, not left to chance.
- **Seeds.** Every run is reproducible. Tests compare whole raw data files between runs, and between the two simulator modes.

---

## 2. Unit tests (each part on its own)

**Data processing** (`tests/data/test_analysis.py`, `tests/sequence/test_stability.py`):
- dBm ↔ mW conversions (+3 dBm ≈ 2×, −3 dBm ≈ ½)
- Averaging is done in milliwatts: 0 dBm and −10 dBm average to −2.60 dBm, not −5 dBm
- For small variations the two methods differ by less than 0.001 dB
- Limits are **inclusive**: exactly −3.000 and +3.000 pass, −3.001 and +3.001 fail
- Min, max and sample standard deviation against hand-calculated values
- An impossible value fails the channel and is **excluded** from the statistics
- No valid readings: statistics are blank and the channel fails with `no_valid_readings`
- Only the final attempt of each reading counts; retries are counted separately
- A high retry rate produces a warning but not a failure
- `max_bad_readings`: with 2 bad readings out of 3, a channel fails when 0 or 1 are allowed and passes when 2 are; bad readings stay out of the statistics either way, and allowing them never rescues a channel whose average is outside the limits
- Temperature failure fails the device even when every channel passes
- Stability: an invalid reading restarts the window, and a temperature **still rising through** the tolerance band is not counted as stable

**Reply parsing** (`tests/core/test_instrument.py`, `tests/instruments/*/test_driver.py`, using a scripted fake instrument):
- Each kind of reply is classified correctly: valid, out-of-spec (still VALID), `ERR…`, out of range (INVALID, value kept), NaN/inf, garbled, empty, no reply
- A `POW:ALL?` reply with a damaged value, a lost value, a missing field, an error code and an out-of-range value: each channel is judged on its own and the good ones stay VALID
- Unreadable whole reply → whole-message GARBLED, raw text preserved on every row
- Meter inputs map to the right device channels (input 1 → channel 9 on a second meter)
- A lost connection raises rather than being hidden
- The TEC switches its output **off** on close; close is safe to call twice, and still disconnects if switching off fails
- Setup commands are retried on timeout, then give up with a clear error

**Structure and plumbing** (`tests/core/`, `tests/simulation/test_bench.py`):
- **Adding an instrument really is one folder:** a test drops a new folder next to `tec/` and `opm/`, and the registry finds it with no other change
- Every instrument folder is complete: settings, a driver that fills a known role, and a simulator, with the config `type` matching its registered name
- Two instruments can't claim the same name
- Roles can't be used half-implemented, and any temperature controller switches its output off when made safe
- PyVISA over a **real network socket** against a tiny fake instrument: a reply is received, silence is a timeout (and the connection stays usable), and an instrument that has vanished is reported as a lost connection rather than a timeout
- The bench's control commands, port assignment (automatic and explicit), detection of busy ports, and the in-process transport

**Charts** (`tests/data/test_plots.py`, skipped if matplotlib isn't installed):
- Every reading lands in the right colour group (valid, valid after retry, timeout/garbled, invalid, not measured)
- On the flaky-meter run, the status grid's counts agree with the summary for every channel
- A run saves three charts per condition (six for the two-temperature config), and none when `save_plots` is off
- **Charts can never break a run:** with matplotlib missing, or with a chart that fails to draw, the run still completes, the summary is still written, and the log says why there are no charts
- `ate plot` draws charts for an old run, and reports an error for a folder with no data
- `ate reprocess --config` with new limits draws a second, separately named set of charts; the original charts are byte-for-byte unchanged
- The power chart says how many bad readings the limits allow

Charts are left out of every other test (`save_plots: false` in the test helper), so the suite stays fast.

**Configuration** (`tests/config/test_config.py`): each validation rule produces its plain-language error. The rules covered are reversed limits, offsets/channel-count mismatch, interval shorter than averaging, timeout not longer than averaging, a stable window longer than max wait, a time scale outside 0–1, fault rate > 1, allowing as many bad readings as are taken, typos in setting names (including inside an instrument's own settings), duplicate instrument names, unknown instrument types (with the list of known ones), a channel wired to two meters or none, too many channels for a meter, `auto` addresses with real hardware, no TEC, and duplicate condition names. Also checked:
- each instrument's settings are handled by the settings model in its own folder
- every example config is valid
- `extends:` inherits everything not overridden
- a saved `config_used.yaml` loads back identical to the original

---

## 3. Simulator tests

`tests/instruments/tec/test_simulator.py`, `tests/instruments/opm/test_simulator.py` and `tests/simulation/`:

| Check | Where | Expected | Result |
|---|---|---|---|
| Meter noise over 4,000 readings at 100 ms averaging | `instruments/opm/` | σ = 0.050 dB | within 5% |
| Meter noise at 400 ms averaging | `instruments/opm/` | σ = 0.025 dB (4× longer → half) | within 5% |
| Mean power of channel 1 at its reference temperature | `instruments/opm/` | nominal + offset = 0.10 dBm | within 0.005 dB |
| One reading's duration | `instruments/opm/` | equals averaging time | exact |
| Input with nothing wired to it | `instruments/opm/` | dark level, about −75 dBm | ✓ |
| TEC before being switched on | `instruments/tec/` | stays at room temperature (25 °C) | exact |
| TEC after one time constant (8 s) from 25 °C | `instruments/tec/` | 40 − 15/e = 34.48 °C | exact |
| TEC with steady-state error 0.8 °C | `instruments/tec/` | 40.8 °C | exact |
| TEC switched off | `instruments/tec/` | cools back to room temperature | exact |
| TEC reading wobble | `instruments/tec/` | σ = 0.02 °C | within 10% |
| Device link: TEC 40 °C → 30 °C, read through the meter | `simulation/` | +1.0 dB (−0.1 dB/°C) | within 0.01 dB |
| Two TECs on one device | `simulation/` | device sits at their average | exact |
| Fault rate of 10% | `simulation/` | about 10% of rolls fail | within 1% |
| Forced fault | `simulation/`, both instruments | happens exactly once, on the right channel only | ✓ |
| Forcing a fault | `simulation/` | doesn't shift any later random decisions | ✓ |
| 50% channel-invalid rate vs none | `instruments/opm/` | every remaining valid value identical | ✓ |
| Same seed / different seed | both instruments, `simulation/` | identical / different sequences | ✓ |
| Kill after N measurement queries | `simulation/` | N answers, then dead for good | ✓ |
| 5,000 garbled values | `simulation/` | none can be read as a number | ✓ |

---

## 4. Full-run tests

`tests/system/test_full_runs.py`, `tests/system/test_network.py`, `tests/system/test_cli.py` and `tests/data/test_storage_and_reports.py`:
- A clean run passes and writes every file, with the exact column sets. There are 400 measurement rows (all VALID) and 50 monitoring rows, and every row carries the run ID and serial.
- Each channel's average matches its configured offset
- Every run gets its own folder
- **Reproducibility:** the same seed gives an identical raw file (except run ID and real timestamps), and a different seed gives a different one
- The seed is always saved, even when it was chosen automatically
- **Re-processing** a run gives a byte-identical summary. Re-processing with stricter limits writes a new file and leaves the original untouched.
- Per-channel mode: channels are measured one at a time
- Two temperatures: at 25 °C every channel reads about 1.5 dB brighter than at 40 °C (the device link, end to end)
- Two meters, 16 channels: 16 summary rows, correctly mapped to meter and input
- Adding a second meter leaves the first meter's faults and statuses identical; values differ by under 0.01 dB, only because the extra meter takes extra time
- **Network vs in-process:** the same seed gives an identical raw file whether the simulators run over real network ports (PyVISA) or inside the program
- Raw rows reach the file **while the run is still going** (read back before the recorder closes); missing values are blank, never zero
- `run_info.json` records the status, seed, result, and each instrument's name and identity
- **Command line:** exit code 0 for a passing device, 1 for a failing one, 2 for a bad config (which prints the reason and creates no results folder) or a missing run folder for `reprocess`; `ate validate` lists the instruments

---

## 5. Error scenarios (forced failures)

Communication problems are tested in `tests/sequence/test_retry_policy.py`, real-but-failing devices and lost instruments in `tests/system/test_failure_scenarios.py`, and network cases in `tests/system/test_network.py`. These are the scenarios from design section 13.3.

| Scenario | Verified outcome |
|---|---|
| One `POW:ALL?` reply lost | 8 TIMEOUT rows (attempt 1), 8 VALID rows (attempt 2), device still passes, 1 retry counted per channel |
| Two lost in a row | That reading is TIMEOUT on every channel; every channel fails validity |
| Whole reply unreadable | GARBLED rows with the damaged text preserved; retried; recovers |
| One channel damaged | **Only that channel** is re-read (attempt 2); the other 7 keep their first reading |
| One channel impossible value | INVALID, raw reply kept, **not retried**; only that channel fails |
| Lost reply in one-channel-at-a-time mode | Same retry rule: attempt 1 TIMEOUT, attempt 2 VALID |
| TEC reading lost | Retried once; recovers |
| TEC invalid reading late in settling | Stability window restarts; settles later; still passes |
| Weak channel (−4 dB) | All 50 readings VALID; channel fails on `avg_below_min` |
| TEC never settles (0.8 °C hot) | 2 settling attempts, verification FAIL, light still measured (≈0.08 dB low because the device is hot), device FAIL |
| ...with `continue_on_fail: false` | All 400 optical readings recorded as NOT_MEASURED, with the reason |
| One of two meters dies mid-run | Its channels: 10 valid + 40 NOT_MEASURED, FAIL. The other meter's channels pass. Status `COMPLETE_WITH_ERRORS` |
| Every meter dies | Status `ABORTED`; data so far kept; summary still written; TEC output switched off |
| TEC dies | `ABORTED` with reason; every planned reading accounted for (measured or NOT_MEASURED) |
| Ctrl+C mid-run | `ABORTED`; the 4 rounds already measured are kept; TEC output switched off |
| **Network:** forced timeout | A real PyVISA timeout fires (the simulator stays silent); retry recovers |
| **Network:** meter dies | The lost connection is detected through PyVISA; other meter carries on |
| **Network:** port already in use | Clear error **before** any results folder is created |

---

## 6. Statistical check

`tests/system/test_statistics.py` runs 200 seeded runs with the default fault rates.

The design predicts a device pass rate of about **66%**:
- 0.999⁴⁰⁰ ≈ 67% chance of no impossible value in 400 readings
- × about 0.99 for communication failures that also fail their retry

**Measured: 63.0% over 200 runs.** That's within the expected statistical spread (±3.3% standard error). This confirms the simulator, the retry policy and the analysis agree with the math in design section 2.7.

---

## 7. Manual checks

Beyond the automated suite, the sample runs in `sample_results/` were produced through the real network bench. Their raw files and logs were inspected by hand.

After the restructure into one folder per instrument, seed 42 was run again over the network. Its raw data was **byte-for-byte identical** to the sample below (apart from run ID and real timestamps), which confirms the restructure changed no behaviour and the samples are still valid.

| Sample | Config | Seed | Result |
|---|---|---|---|
| `20261123-182555_SN-0001_904c` | default | 2 | PASS |
| `20261123-182555_SN-0001_b1d8` | default | 42 | FAIL: one impossible reading (`3:ERR:NO_SIGNAL`) on channel 3 |
| `20261123-182557_SN-WEAK5_4b30` | weak_channel | 2 | FAIL: channel 5 averages −4.00 dBm |
| `20261123-182558_SN-FLAKY_b4d5` | flaky_meter | 2 | FAIL: 492 VALID, 40 TIMEOUT, 24 GARBLED, 5 INVALID rows, each handled by the right rule |
| `20261123-182602_SN-HOT_f4b5` | tec_wont_settle | 2 | FAIL: temperature never verified, light still measured |
| `20261123-182603_SN-0001_5873` | two_temperatures | 2 | PASS at 25 °C and 40 °C |

The charts in each sample folder were added afterwards with `ate plot`, from the saved raw data (the data itself is unchanged). Each chart was checked by eye against its run: channel 5 below the lower limit in the weak-channel run, the TEC levelling off above the band in the hot run, and retry columns and single re-read squares in the flaky run.

---

## 8. Issues found by testing

Testing found and fixed three real problems during development:

1. **Temperature "verified" while still rising.** The original stability rule only required readings to stay within ±0.5 °C for 10 s. A controller heading towards 40.8 °C spends about 11 s inside 39.5–40.5 °C on the way up, so the faulty TEC wrongly passed. The fix adds a drift limit: across the window, readings must not change by more than `max_drift_c` (0.1 °C). The temperature must have **stopped moving**, not just be passing through. A side benefit: the verified temperature on a healthy run rose from 39.71 °C to 39.91 °C, because verification now waits for real settling.
2. **Exactly −3.0 dBm failing.** Converting −3 dBm to milliwatts and back gives −3.0000000000000004 in floating point. Limits are now compared at a fixed resolution of 0.000001 dB, so they're truly inclusive.
3. **Two runs crashing on the same folder name.** Found by testing a fresh copy of the repository. Each run's folder is named from the time (to the second), the serial number and a 4-character random code (65,536 possibilities). The statistical check starts about 70 runs per second, so occasionally two drew the same code. The program correctly refused to reuse the folder, but then crashed (`FileExistsError`, about 1 full test run in 8). Now it draws a new code and tries again; existing folders are still never reused or overwritten. A test forces a name clash on purpose, and the full suite then passed 12 times in a row.

## 9. Known limitations

- Fault injection applies to measurement queries (`TEMP?`, `POW?`, `POW:ALL?`). Setup commands are assumed reliable in simulation, though the driver still retries them and fails clearly if they never answer.
- A truncated reply that still looks like valid numbers (e.g. `0.0` from `0.04`) can't be detected without a checksum, which this command language doesn't have. The simulator doesn't generate such replies. A real protocol would add a checksum or a value count.
- Real-hardware mode (`simulation.enabled: false`) is implemented but, without hardware, was only verified to the extent that the network mode uses the same PyVISA code path.
- PyVISA (with `pyvisa-py`) opens network connections lazily: connecting to an address where nothing is listening doesn't fail until the first command is sent. Because connecting an instrument always starts with `*IDN?`, this still surfaces immediately as a clear "lost connection" error (verified in `tests/core/test_transport.py`).
